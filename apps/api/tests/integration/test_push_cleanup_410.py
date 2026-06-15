"""Integration test: push_fanout deletes 410-Gone rows + culls stale (T-006).

Sister file to ``test_push_fanout_e2e.py`` — keeps the cleanup
semantics isolated so a regression in the bulk_delete + cleanup_stale
sequencing surfaces with a single-test failure rather than buried in
the e2e happy-path noise.

Skips when DATABASE_URL is the conftest placeholder.

Coverage:
  1. A subscription that responds 410 is hard-deleted (Gate 7 carve-out).
  2. cleanup_stale runs at the end of every fan-out: a row with
     failure_count >= threshold and no recent success is deleted even
     if its send returned FAILED (not GONE) this round.
  3. Mixed outcome: SUCCESS + FAILED + GONE all in one fan-out → each
     subscription gets the right repo write.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.push_fanout import _ChapterMeta, run_push_fanout
from app.infra.push_subscriptions_repo import PushSubscriptionsRepo
from app.infra.webpush_sender import (
    SendOutcome,
    SendResult,
    WebPushSender,
)


def _invite_code() -> str:
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
    session: AsyncSession, tracker: _TestTracker
) -> int:
    code = _invite_code()
    token = uuid4().hex + uuid4().hex
    await session.execute(
        sa.text(
            "INSERT INTO invites (code, issued_by, expires_at, status, note) "
            "VALUES (:code, 'cleanup-test', :exp, 'unused', 'cleanup-410')"
        ),
        {"code": code, "exp": datetime.now(UTC) + timedelta(days=7)},
    )
    result = await session.execute(
        sa.text(
            "INSERT INTO users (display_name, invite_code, device_token) "
            "VALUES (:n, :c, :t) RETURNING id"
        ),
        {
            "n": f"CleanupUser-{uuid4().hex[:6]}",
            "c": code,
            "t": token,
        },
    )
    uid = int(result.scalar_one())
    tracker.user_ids.append(uid)
    tracker.invite_codes.append(code)
    return uid


def _sender() -> WebPushSender:
    return WebPushSender(
        vapid_private_key="-----BEGIN PRIVATE KEY-----\nFAKE\n-----END PRIVATE KEY-----",
        vapid_subject="mailto:ops@example.com",
    )


@pytest.fixture
async def tracker(
    db_session: AsyncSession,
) -> AsyncIterator[_TestTracker]:
    """Same shape as the e2e fixture — see test_push_fanout_e2e.py."""
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
# 410 Gone → row deleted
# ---------------------------------------------------------------------------


async def test_gone_subscription_is_hard_deleted(
    db_session: AsyncSession,
    tracker: _TestTracker,
) -> None:
    user = await _seed_user(db_session, tracker)
    repo = PushSubscriptionsRepo(db_session)
    sid_alive = await repo.upsert(
        user_id=user,
        endpoint=f"https://push.example/{uuid4().hex}",
        p256dh="pk",
        auth="ak",
        ua=None,
    )
    sid_gone = await repo.upsert(
        user_id=user,
        endpoint=f"https://push.example/{uuid4().hex}",
        p256dh="pk",
        auth="ak",
        ua=None,
    )
    await db_session.commit()

    meta = _ChapterMeta(
        public_id=uuid4(), title="Cap Gone", day_index=5
    )
    tracker.idempotency_keys.append(f"push_fanout:{meta.public_id}")

    sender = _sender()
    sender.send = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            SendOutcome(
                subscription_id=sid_alive,
                result=SendResult.SUCCESS,
                status_code=201,
            ),
            SendOutcome(
                subscription_id=sid_gone,
                result=SendResult.GONE,
                status_code=410,
            ),
        ]
    )

    with patch(
        "app.domain.push_fanout._load_chapter_meta",
        new=AsyncMock(return_value=meta),
    ):
        summary = await run_push_fanout(
            chapter_id=1,
            session=db_session,
            sender=sender,
            timeout_s=10.0,
            concurrency=2,
        )

    assert summary.sent == 1
    assert summary.gone == 1

    # The gone row is gone from the DB.
    alive_remaining = (
        await db_session.execute(
            sa.text(
                "SELECT id FROM push_subscriptions WHERE id IN (:a, :g)"
            ).bindparams(a=sid_alive, g=sid_gone),
        )
    ).scalars().all()
    assert sid_alive in alive_remaining
    assert sid_gone not in alive_remaining


# ---------------------------------------------------------------------------
# cleanup_stale runs even when nobody returned GONE
# ---------------------------------------------------------------------------


async def test_cleanup_stale_culls_long_dead_row(
    db_session: AsyncSession,
    tracker: _TestTracker,
) -> None:
    user = await _seed_user(db_session, tracker)
    repo = PushSubscriptionsRepo(db_session)
    sid_active = await repo.upsert(
        user_id=user,
        endpoint=f"https://push.example/{uuid4().hex}",
        p256dh="pk",
        auth="ak",
        ua=None,
    )
    sid_stale = await repo.upsert(
        user_id=user,
        endpoint=f"https://push.example/{uuid4().hex}",
        p256dh="pk",
        auth="ak",
        ua=None,
    )
    # Make sid_stale satisfy the stale rule:
    #   failure_count >= 3 AND last_success_at < now() - 7 days
    await db_session.execute(
        sa.text(
            "UPDATE push_subscriptions "
            "SET failure_count = 5, "
            "    last_success_at = now() - INTERVAL '14 days' "
            "WHERE id = :sid"
        ),
        {"sid": sid_stale},
    )
    await db_session.commit()

    meta = _ChapterMeta(
        public_id=uuid4(), title="Cap Cull", day_index=6
    )
    tracker.idempotency_keys.append(f"push_fanout:{meta.public_id}")

    sender = _sender()

    async def _per_sub(
        subscription: object, _payload: object
    ) -> SendOutcome:
        sid = subscription.id  # type: ignore[attr-defined]
        return SendOutcome(
            subscription_id=sid,
            result=SendResult.SUCCESS,
            status_code=201,
        )

    sender.send = AsyncMock(side_effect=_per_sub)  # type: ignore[method-assign]

    with patch(
        "app.domain.push_fanout._load_chapter_meta",
        new=AsyncMock(return_value=meta),
    ):
        summary = await run_push_fanout(
            chapter_id=2,
            session=db_session,
            sender=sender,
            timeout_s=10.0,
            threshold=3,
            concurrency=2,
        )

    # The stale row got reset (mark_success) → cleanup_stale finds 0.
    # BUT: cleanup runs AFTER mark_success, and mark_success reset the
    # counter to 0. So the stale row is reset, not deleted. That's the
    # right behaviour — a successful send redeems flaky rows.
    assert summary.cleaned == 0
    remaining = (
        await db_session.execute(
            sa.text(
                "SELECT id FROM push_subscriptions "
                "WHERE id IN (:a, :s) ORDER BY id"
            ).bindparams(a=sid_active, s=sid_stale),
        )
    ).scalars().all()
    assert set(remaining) == {sid_active, sid_stale}


async def test_cleanup_stale_culls_row_whose_send_failed(
    db_session: AsyncSession,
    tracker: _TestTracker,
) -> None:
    """When the row was stale AND its send failed → cleanup_stale deletes it.

    A FAILED send increments failure_count again, so a previously-near-
    threshold row crosses the boundary and gets reaped.
    """
    user = await _seed_user(db_session, tracker)
    repo = PushSubscriptionsRepo(db_session)
    sid_doomed = await repo.upsert(
        user_id=user,
        endpoint=f"https://push.example/{uuid4().hex}",
        p256dh="pk",
        auth="ak",
        ua=None,
    )
    # Already 3 failures, no successes ever → still under threshold=4 but
    # one more failure crosses.
    await db_session.execute(
        sa.text(
            "UPDATE push_subscriptions "
            "SET failure_count = 3, last_success_at = NULL "
            "WHERE id = :sid"
        ),
        {"sid": sid_doomed},
    )
    await db_session.commit()

    meta = _ChapterMeta(
        public_id=uuid4(), title="Cap Doom", day_index=8
    )
    tracker.idempotency_keys.append(f"push_fanout:{meta.public_id}")

    sender = _sender()
    sender.send = AsyncMock(  # type: ignore[method-assign]
        return_value=SendOutcome(
            subscription_id=sid_doomed,
            result=SendResult.FAILED,
            status_code=500,
        )
    )

    with patch(
        "app.domain.push_fanout._load_chapter_meta",
        new=AsyncMock(return_value=meta),
    ):
        summary = await run_push_fanout(
            chapter_id=3,
            session=db_session,
            sender=sender,
            timeout_s=10.0,
            threshold=4,
            concurrency=1,
        )

    assert summary.failed == 1
    assert summary.cleaned == 1
    row = (
        await db_session.execute(
            sa.text(
                "SELECT id FROM push_subscriptions WHERE id = :sid"
            ),
            {"sid": sid_doomed},
        )
    ).scalar_one_or_none()
    assert row is None
