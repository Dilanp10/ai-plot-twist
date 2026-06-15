"""Integration tests: push_fanout orchestrator end-to-end (T-006).

Uses a real Postgres for the repo/idempotency reads, and patches
:class:`WebPushSender` with an ``AsyncMock`` so no network I/O happens.
Each test seeds invites + users + subscriptions, runs the orchestrator,
and asserts the aggregate outcome.

Skips when DATABASE_URL is the conftest placeholder.

Coverage:
  1. Happy path: 3 subs all return SUCCESS → sent=3, no rows deleted.
  2. Idempotency: second call for the same chapter returns
     skipped_idempotent=True and never invokes the sender.
  3. Empty audience: no subscriptions → sent=0, skip senders, no commit
     failures.
  4. Deadline exceeded: a sender that sleeps past the timeout returns
     partial outcomes, ``deadline_exceeded=True``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.push_fanout import (
    _ChapterMeta,
    run_push_fanout,
)
from app.infra.push_subscriptions_repo import PushSubscriptionsRepo
from app.infra.webpush_sender import (
    SendOutcome,
    SendResult,
    WebPushSender,
)

# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _invite_code() -> str:
    """Build a code matching ck_invites_code_format ^[A-Z2-7]{4}-[A-Z2-7]{4}$."""
    src = uuid4().hex.upper()
    valid = "".join(c for c in src if c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
    while len(valid) < 8:
        valid += "A"
    return f"{valid[:4]}-{valid[4:8]}"


@dataclass
class _TestTracker:
    user_ids: list[int]
    invite_codes: list[str]
    idempotency_keys: list[str]


async def _seed_user(
    session: AsyncSession,
    tracker: _TestTracker,
    *,
    is_banned: bool = False,
) -> int:
    code = _invite_code()
    token = uuid4().hex + uuid4().hex
    await session.execute(
        sa.text(
            "INSERT INTO invites (code, issued_by, expires_at, status, note) "
            "VALUES (:code, 'fanout-test', :exp, 'unused', 'fanout-e2e')"
        ),
        {"code": code, "exp": datetime.now(UTC) + timedelta(days=7)},
    )
    name = f"FanoutUser-{uuid4().hex[:6]}"
    banned_sql = ", is_banned" if is_banned else ""
    banned_val = ", TRUE" if is_banned else ""
    result = await session.execute(
        sa.text(
            f"INSERT INTO users (display_name, invite_code, device_token"
            f"{banned_sql}) "
            f"VALUES (:name, :code, :token{banned_val}) RETURNING id"
        ),
        {"name": name, "code": code, "token": token},
    )
    uid = int(result.scalar_one())
    tracker.user_ids.append(uid)
    tracker.invite_codes.append(code)
    return uid


async def _seed_subs(
    session: AsyncSession, user_id: int, count: int
) -> list[int]:
    repo = PushSubscriptionsRepo(session)
    return [
        await repo.upsert(
            user_id=user_id,
            endpoint=f"https://push.example/{uuid4().hex}",
            p256dh="pk",
            auth="ak",
            ua=None,
        )
        for _ in range(count)
    ]


def _meta(public_id: UUID | None = None) -> _ChapterMeta:
    return _ChapterMeta(
        public_id=public_id or uuid4(),
        title="Capítulo Test",
        day_index=7,
    )


def _sender() -> WebPushSender:
    return WebPushSender(
        vapid_private_key="-----BEGIN PRIVATE KEY-----\nFAKE\n-----END PRIVATE KEY-----",
        vapid_subject="mailto:ops@example.com",
    )


@pytest.fixture
async def tracker(
    db_session: AsyncSession,
) -> AsyncIterator[_TestTracker]:
    """Track every row the test commits + wipe them on teardown.

    db_session rolls back at teardown, but the orchestrator commits
    inside ``run_push_fanout`` so user / subscription / idempotency
    rows survive across tests unless we explicitly delete them. The
    teardown deletes in dependency order: subs → users → invites →
    idempotency_keys.
    """
    t = _TestTracker(user_ids=[], invite_codes=[], idempotency_keys=[])
    yield t
    if t.user_ids:
        await db_session.execute(
            sa.text(
                "DELETE FROM push_subscriptions WHERE user_id = ANY(:u)"
            ),
            {"u": t.user_ids},
        )
        await db_session.execute(
            sa.text("DELETE FROM users WHERE id = ANY(:u)"),
            {"u": t.user_ids},
        )
    if t.invite_codes:
        await db_session.execute(
            sa.text("DELETE FROM invites WHERE code = ANY(:c)"),
            {"c": t.invite_codes},
        )
    if t.idempotency_keys:
        await db_session.execute(
            sa.text("DELETE FROM idempotency_keys WHERE key = ANY(:k)"),
            {"k": t.idempotency_keys},
        )
    await db_session.commit()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_fanout_happy_path_all_success(
    db_session: AsyncSession,
    tracker: _TestTracker,
) -> None:
    user = await _seed_user(db_session, tracker)
    sub_ids = await _seed_subs(db_session, user, 3)
    await db_session.commit()

    meta = _meta()
    tracker.idempotency_keys.append(f"push_fanout:{meta.public_id}")

    sender = _sender()
    sender.send = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            SendOutcome(subscription_id=sid, result=SendResult.SUCCESS,
                        status_code=201)
            for sid in sub_ids
        ]
    )

    with patch(
        "app.domain.push_fanout._load_chapter_meta",
        new=AsyncMock(return_value=meta),
    ):
        summary = await run_push_fanout(
            chapter_id=999,
            session=db_session,
            sender=sender,
            timeout_s=10.0,
        )

    assert summary.sent == 3
    assert summary.failed == 0
    assert summary.gone == 0
    assert summary.skipped_idempotent is False
    assert summary.deadline_exceeded is False
    assert sender.send.await_count == 3

    # mark_success runs → last_success_at is set on every row.
    rows = (
        await db_session.execute(
            sa.text(
                "SELECT id, last_success_at "
                "FROM push_subscriptions WHERE id = ANY(:ids)"
            ),
            {"ids": sub_ids},
        )
    ).mappings().all()
    assert all(r["last_success_at"] is not None for r in rows)


# ---------------------------------------------------------------------------
# Idempotency short-circuit
# ---------------------------------------------------------------------------


async def test_fanout_idempotent_second_call_short_circuits(
    db_session: AsyncSession,
    tracker: _TestTracker,
) -> None:
    user = await _seed_user(db_session, tracker)
    await _seed_subs(db_session, user, 2)
    await db_session.commit()

    meta = _meta()
    tracker.idempotency_keys.append(f"push_fanout:{meta.public_id}")

    sender = _sender()
    sender.send = AsyncMock(  # type: ignore[method-assign]
        return_value=SendOutcome(
            subscription_id=1, result=SendResult.SUCCESS, status_code=201
        )
    )

    with patch(
        "app.domain.push_fanout._load_chapter_meta",
        new=AsyncMock(return_value=meta),
    ):
        first = await run_push_fanout(
            chapter_id=999,
            session=db_session,
            sender=sender,
            timeout_s=10.0,
        )
        sender.send.reset_mock()
        second = await run_push_fanout(
            chapter_id=999,
            session=db_session,
            sender=sender,
            timeout_s=10.0,
        )

    assert first.skipped_idempotent is False
    assert second.skipped_idempotent is True
    assert second.sent == 0
    assert sender.send.await_count == 0


# ---------------------------------------------------------------------------
# Empty audience
# ---------------------------------------------------------------------------


async def test_fanout_no_subscriptions_returns_zero_counts(
    db_session: AsyncSession,
    tracker: _TestTracker,
) -> None:
    # No subs seeded.
    meta = _meta()
    tracker.idempotency_keys.append(f"push_fanout:{meta.public_id}")

    sender = _sender()
    sender.send = AsyncMock()  # type: ignore[method-assign]

    with patch(
        "app.domain.push_fanout._load_chapter_meta",
        new=AsyncMock(return_value=meta),
    ):
        summary = await run_push_fanout(
            chapter_id=999,
            session=db_session,
            sender=sender,
            timeout_s=10.0,
        )

    assert summary == summary.__class__(
        sent=0,
        failed=0,
        gone=0,
        cleaned=0,
        deadline_exceeded=False,
        skipped_idempotent=False,
    )
    assert sender.send.await_count == 0


# ---------------------------------------------------------------------------
# Deadline exceeded
# ---------------------------------------------------------------------------


async def test_fanout_deadline_exceeded_returns_partial(
    db_session: AsyncSession,
    tracker: _TestTracker,
) -> None:
    user = await _seed_user(db_session, tracker)
    await _seed_subs(db_session, user, 2)
    await db_session.commit()

    meta = _meta()
    tracker.idempotency_keys.append(f"push_fanout:{meta.public_id}")

    async def _hang(*_args: object, **_kwargs: object) -> SendOutcome:
        await asyncio.sleep(3600)
        raise AssertionError("should not reach")

    sender = _sender()
    sender.send = AsyncMock(side_effect=_hang)  # type: ignore[method-assign]

    with patch(
        "app.domain.push_fanout._load_chapter_meta",
        new=AsyncMock(return_value=meta),
    ):
        summary = await run_push_fanout(
            chapter_id=999,
            session=db_session,
            sender=sender,
            timeout_s=0.05,
            concurrency=2,
        )

    assert summary.deadline_exceeded is True
    assert summary.sent == 0
    assert summary.failed == 0
    assert summary.skipped_idempotent is False
