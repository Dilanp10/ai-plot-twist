"""Integration tests: PushSubscriptionsRepo (T-003).

Hits a real Postgres. Each test creates its own seeded invite + user
rows via the fixtures helper, exercises the repo, and rolls back via
``db_session``. Skips when DATABASE_URL is the conftest placeholder.

Coverage:
  1. upsert inserts a fresh row and returns its id.
  2. upsert on a duplicate endpoint rebinds it + resets failure_count.
  3. list_active_for_user returns newest-first per user.
  4. list_active_all excludes banned users.
  5. delete_by_id_for_user enforces ownership.
  6. mark_success resets failure_count and bumps last_success_at.
  7. mark_failure increments failure_count.
  8. bulk_delete removes multiple ids in one statement (returns count).
  9. cleanup_stale honours the threshold AND the 7-day floor (R-005).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.push_subscriptions_repo import PushSubscriptionsRepo

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _b32(prefix: str) -> str:
    """Generate a valid invite-code-format 4-4 base32 string from a prefix."""
    base = (prefix + "AAAA")[:4].upper()
    tail = (uuid4().hex.upper().translate(str.maketrans("01", "AA")))[:4]
    # Filter to RFC 4648 base32 (A-Z, 2-7). Replace 8/9 with 2/3.
    tail = (
        tail.replace("8", "2")
        .replace("9", "3")
        .replace("0", "A")
        .replace("1", "A")
    )
    return f"{base}-{tail}"


async def _seed_user(
    session: AsyncSession,
    *,
    is_banned: bool = False,
) -> int:
    """Insert a fresh invite + user. Returns user id."""
    code = _b32("MIG")
    device_token = uuid4().hex + uuid4().hex  # 64 chars
    expires_at = datetime.now(UTC) + timedelta(days=7)
    await session.execute(
        sa.text(
            "INSERT INTO invites (code, issued_by, expires_at, status, note) "
            "VALUES (:code, 'repo-test', :exp, 'unused', 'push-repo-test')"
        ),
        {"code": code, "exp": expires_at},
    )
    name = f"PushRepoUser-{uuid4().hex[:6]}"
    banned_sql = ", is_banned" if is_banned else ""
    banned_val = ", TRUE" if is_banned else ""
    result = await session.execute(
        sa.text(
            f"INSERT INTO users (display_name, invite_code, device_token"
            f"{banned_sql}) "
            f"VALUES (:name, :code, :token{banned_val}) RETURNING id"
        ),
        {"name": name, "code": code, "token": device_token},
    )
    return int(result.scalar_one())


@pytest.fixture
async def repo(db_session: AsyncSession) -> AsyncIterator[PushSubscriptionsRepo]:
    yield PushSubscriptionsRepo(db_session)


def _endpoint() -> str:
    return f"https://push.example/{uuid4().hex}"


# ---------------------------------------------------------------------------
# upsert
# ---------------------------------------------------------------------------


async def test_upsert_inserts_new_row(
    db_session: AsyncSession,
    repo: PushSubscriptionsRepo,
) -> None:
    user_id = await _seed_user(db_session)
    sid = await repo.upsert(
        user_id=user_id,
        endpoint=_endpoint(),
        p256dh="pk",
        auth="ak",
        ua="UA/1.0",
    )
    assert sid > 0


async def test_upsert_on_duplicate_endpoint_rebinds_and_resets_count(
    db_session: AsyncSession,
    repo: PushSubscriptionsRepo,
) -> None:
    user_a = await _seed_user(db_session)
    user_b = await _seed_user(db_session)
    endpoint = _endpoint()

    sid_a = await repo.upsert(
        user_id=user_a,
        endpoint=endpoint,
        p256dh="pk-a",
        auth="ak-a",
        ua="UA/A",
    )
    # Force the failure_count up so the reset is observable.
    await repo.mark_failure(sid_a)
    await repo.mark_failure(sid_a)

    sid_b = await repo.upsert(
        user_id=user_b,
        endpoint=endpoint,
        p256dh="pk-b",
        auth="ak-b",
        ua="UA/B",
    )
    assert sid_b == sid_a, "endpoint UNIQUE → upsert hits the same row"

    row = (
        await db_session.execute(
            sa.text(
                "SELECT user_id, p256dh_key, auth_key, user_agent, failure_count "
                "FROM push_subscriptions WHERE id = :sid"
            ),
            {"sid": sid_a},
        )
    ).mappings().one()
    assert row["user_id"] == user_b
    assert row["p256dh_key"] == "pk-b"
    assert row["auth_key"] == "ak-b"
    assert row["user_agent"] == "UA/B"
    assert row["failure_count"] == 0


# ---------------------------------------------------------------------------
# list_active_for_user
# ---------------------------------------------------------------------------


async def test_list_active_for_user_returns_newest_first(
    db_session: AsyncSession,
    repo: PushSubscriptionsRepo,
) -> None:
    user_id = await _seed_user(db_session)
    sid_old = await repo.upsert(
        user_id=user_id, endpoint=_endpoint(), p256dh="p1", auth="a1", ua=None
    )
    # Backdate the first row so created_at ordering is unambiguous in tests.
    await db_session.execute(
        sa.text(
            "UPDATE push_subscriptions "
            "SET created_at = now() - INTERVAL '1 hour' "
            "WHERE id = :sid"
        ),
        {"sid": sid_old},
    )
    sid_new = await repo.upsert(
        user_id=user_id, endpoint=_endpoint(), p256dh="p2", auth="a2", ua=None
    )

    rows = await repo.list_active_for_user(user_id)
    assert [r.id for r in rows] == [sid_new, sid_old]


# ---------------------------------------------------------------------------
# list_active_all
# ---------------------------------------------------------------------------


async def test_list_active_all_excludes_banned_users(
    db_session: AsyncSession,
    repo: PushSubscriptionsRepo,
) -> None:
    active = await _seed_user(db_session)
    banned = await _seed_user(db_session, is_banned=True)
    sid_active = await repo.upsert(
        user_id=active, endpoint=_endpoint(), p256dh="p", auth="a", ua=None
    )
    await repo.upsert(
        user_id=banned, endpoint=_endpoint(), p256dh="p", auth="a", ua=None
    )

    rows = await repo.list_active_all()
    ids = {r.id for r in rows}
    assert sid_active in ids
    banned_rows = [r for r in rows if r.user_id == banned]
    assert banned_rows == []


# ---------------------------------------------------------------------------
# delete_by_id_for_user
# ---------------------------------------------------------------------------


async def test_delete_by_id_for_user_returns_true_on_match(
    db_session: AsyncSession,
    repo: PushSubscriptionsRepo,
) -> None:
    user_id = await _seed_user(db_session)
    sid = await repo.upsert(
        user_id=user_id, endpoint=_endpoint(), p256dh="p", auth="a", ua=None
    )
    deleted = await repo.delete_by_id_for_user(
        subscription_id=sid, user_id=user_id
    )
    assert deleted is True

    rows = await repo.list_active_for_user(user_id)
    assert rows == []


async def test_delete_by_id_for_user_returns_false_for_other_user(
    db_session: AsyncSession,
    repo: PushSubscriptionsRepo,
) -> None:
    owner = await _seed_user(db_session)
    intruder = await _seed_user(db_session)
    sid = await repo.upsert(
        user_id=owner, endpoint=_endpoint(), p256dh="p", auth="a", ua=None
    )
    deleted = await repo.delete_by_id_for_user(
        subscription_id=sid, user_id=intruder
    )
    assert deleted is False

    rows = await repo.list_active_for_user(owner)
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# mark_success / mark_failure
# ---------------------------------------------------------------------------


async def test_mark_success_resets_count_and_bumps_timestamp(
    db_session: AsyncSession,
    repo: PushSubscriptionsRepo,
) -> None:
    user_id = await _seed_user(db_session)
    sid = await repo.upsert(
        user_id=user_id, endpoint=_endpoint(), p256dh="p", auth="a", ua=None
    )
    await repo.mark_failure(sid)
    await repo.mark_failure(sid)
    await repo.mark_success(sid)

    row = (
        await db_session.execute(
            sa.text(
                "SELECT failure_count, last_success_at "
                "FROM push_subscriptions WHERE id = :sid"
            ),
            {"sid": sid},
        )
    ).mappings().one()
    assert row["failure_count"] == 0
    assert row["last_success_at"] is not None


async def test_mark_failure_increments(
    db_session: AsyncSession,
    repo: PushSubscriptionsRepo,
) -> None:
    user_id = await _seed_user(db_session)
    sid = await repo.upsert(
        user_id=user_id, endpoint=_endpoint(), p256dh="p", auth="a", ua=None
    )
    await repo.mark_failure(sid)
    await repo.mark_failure(sid)
    await repo.mark_failure(sid)
    count = (
        await db_session.execute(
            sa.text(
                "SELECT failure_count FROM push_subscriptions WHERE id = :sid"
            ),
            {"sid": sid},
        )
    ).scalar_one()
    assert count == 3


# ---------------------------------------------------------------------------
# bulk_delete
# ---------------------------------------------------------------------------


async def test_bulk_delete_removes_multiple_rows(
    db_session: AsyncSession,
    repo: PushSubscriptionsRepo,
) -> None:
    user_id = await _seed_user(db_session)
    sids = [
        await repo.upsert(
            user_id=user_id, endpoint=_endpoint(), p256dh="p", auth="a", ua=None
        )
        for _ in range(3)
    ]
    deleted = await repo.bulk_delete(sids[:2])
    assert deleted == 2

    remaining = await repo.list_active_for_user(user_id)
    assert [r.id for r in remaining] == [sids[2]]


async def test_bulk_delete_empty_is_noop(
    repo: PushSubscriptionsRepo,
) -> None:
    deleted = await repo.bulk_delete([])
    assert deleted == 0


# ---------------------------------------------------------------------------
# cleanup_stale — R-005
# ---------------------------------------------------------------------------


async def test_cleanup_stale_deletes_when_threshold_hit_and_never_succeeded(
    db_session: AsyncSession,
    repo: PushSubscriptionsRepo,
) -> None:
    user_id = await _seed_user(db_session)
    sid = await repo.upsert(
        user_id=user_id, endpoint=_endpoint(), p256dh="p", auth="a", ua=None
    )
    await repo.mark_failure(sid)
    await repo.mark_failure(sid)
    await repo.mark_failure(sid)

    deleted = await repo.cleanup_stale(threshold=3)
    assert deleted == 1


async def test_cleanup_stale_preserves_below_threshold(
    db_session: AsyncSession,
    repo: PushSubscriptionsRepo,
) -> None:
    user_id = await _seed_user(db_session)
    sid = await repo.upsert(
        user_id=user_id, endpoint=_endpoint(), p256dh="p", auth="a", ua=None
    )
    await repo.mark_failure(sid)
    await repo.mark_failure(sid)
    deleted = await repo.cleanup_stale(threshold=3)
    assert deleted == 0


async def test_cleanup_stale_preserves_recent_success_even_above_threshold(
    db_session: AsyncSession,
    repo: PushSubscriptionsRepo,
) -> None:
    user_id = await _seed_user(db_session)
    sid = await repo.upsert(
        user_id=user_id, endpoint=_endpoint(), p256dh="p", auth="a", ua=None
    )
    await repo.mark_failure(sid)
    await repo.mark_failure(sid)
    await repo.mark_failure(sid)
    # Mark a success, then re-fail twice — still flaky but recent-good.
    # mark_success resets to 0, so we have to re-fail to cross the threshold.
    await db_session.execute(
        sa.text(
            "UPDATE push_subscriptions "
            "SET last_success_at = now() - INTERVAL '2 days', "
            "    failure_count = 5 "
            "WHERE id = :sid"
        ),
        {"sid": sid},
    )

    deleted = await repo.cleanup_stale(threshold=3)
    assert deleted == 0


async def test_cleanup_stale_culls_when_last_success_is_old(
    db_session: AsyncSession,
    repo: PushSubscriptionsRepo,
) -> None:
    user_id = await _seed_user(db_session)
    sid = await repo.upsert(
        user_id=user_id, endpoint=_endpoint(), p256dh="p", auth="a", ua=None
    )
    await db_session.execute(
        sa.text(
            "UPDATE push_subscriptions "
            "SET last_success_at = now() - INTERVAL '10 days', "
            "    failure_count = 4 "
            "WHERE id = :sid"
        ),
        {"sid": sid},
    )

    deleted = await repo.cleanup_stale(threshold=3)
    assert deleted == 1
