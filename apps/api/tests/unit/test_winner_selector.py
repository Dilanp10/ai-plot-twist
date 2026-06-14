"""Unit tests: winner_selector.

Module 008 / Task T-001.

Requires a real Postgres connection (DATABASE_URL env var). Skips
automatically when only the conftest placeholder is set.

Each test builds isolated season + chapter + user(s) + twist(s) + votes
and cleans up via CASCADE deletes on the season row.

Coverage:
  - clear_winner: single leader by vote count.
  - two_way_tie: same vote_count → earlier submitted_at wins; tiebreak=True,
    runner_up populated.
  - three_way_tie: same vote_count + same submitted_at → lower id wins;
    tiebreak=True, runner_up is the next in id order.
  - zero_rows: no approved twists → null WinnerPick; auto-continue mode.
"""

from __future__ import annotations

import os
import secrets
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.winner_selector import WinnerPick, pick_winner

_INVITE_ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
_TODAY = date(2026, 6, 14)
_SLUG_PREFIX = "_ws-test-"


# ---------------------------------------------------------------------------
# DB fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def database_url() -> str:
    from tests.conftest import _is_placeholder_database_url

    url = os.environ.get("DATABASE_URL", "")
    if not url or _is_placeholder_database_url(url):
        pytest.skip("DATABASE_URL no apunta a una base real.")
    return url


@pytest.fixture
async def session(database_url: str) -> AsyncSession:  # type: ignore[misc]
    engine = create_async_engine(database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    async with factory() as s:
        yield s
    await engine.dispose()


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------


def _fresh_invite_code() -> str:
    left = "".join(secrets.choice(_INVITE_ALPHA) for _ in range(4))
    right = "".join(secrets.choice(_INVITE_ALPHA) for _ in range(4))
    return f"{left}-{right}"


async def _make_user(session: AsyncSession, display_name: str) -> tuple[int, str]:
    """Insert invite + user; return (user_id, invite_code)."""
    code = _fresh_invite_code()
    expires_at = datetime.now(UTC) + timedelta(days=7)
    await session.execute(
        sa.text(
            "INSERT INTO invites (code, issued_by, expires_at, status) "
            "VALUES (:code, 'ws-test', :expires_at, 'unused')"
        ),
        {"code": code, "expires_at": expires_at},
    )
    result = await session.execute(
        sa.text(
            "INSERT INTO users (display_name, invite_code, device_token) "
            "VALUES (:name, :code, :token) RETURNING id"
        ),
        {"name": display_name, "code": code, "token": (uuid4().hex * 2)[:64]},
    )
    return int(result.scalar_one()), code


async def _make_season_chapter(session: AsyncSession, suffix: str) -> tuple[int, int]:
    result = await session.execute(
        sa.text(
            "INSERT INTO seasons (slug, title, bible_json, started_on, is_active) "
            "VALUES (:slug, 'WS Test Season', '{}', :today, FALSE) RETURNING id"
        ),
        {"slug": f"{_SLUG_PREFIX}{suffix}", "today": _TODAY},
    )
    season_id = int(result.scalar_one())
    result = await session.execute(
        sa.text(
            "INSERT INTO chapters "
            "(season_id, day_index, title, synopsis, manifest_json, status) "
            "VALUES (:sid, 1, 'Ch', 'Syn', '{}', 'generating') RETURNING id"
        ),
        {"sid": season_id},
    )
    return season_id, int(result.scalar_one())


async def _make_twist(
    session: AsyncSession,
    chapter_id: int,
    user_id: int,
    content: str,
    submitted_at: datetime | None = None,
) -> int:
    extra = (
        ", submitted_at"
        if submitted_at is not None
        else ""
    )
    extra_val = (
        ", :submitted_at"
        if submitted_at is not None
        else ""
    )
    params: dict = {"cid": chapter_id, "uid": user_id, "content": content}
    if submitted_at is not None:
        params["submitted_at"] = submitted_at
    result = await session.execute(
        sa.text(
            f"INSERT INTO twists (chapter_id, user_id, content, status{extra}) "
            f"VALUES (:cid, :uid, :content, 'approved'{extra_val}) RETURNING id"
        ),
        params,
    )
    return int(result.scalar_one())


async def _cast_votes(
    session: AsyncSession, twist_id: int, chapter_id: int, count: int, users: list[int]
) -> None:
    """Cast *count* votes for *twist_id* using users[0..count-1]."""
    for i in range(count):
        await session.execute(
            sa.text(
                "INSERT INTO votes (twist_id, user_id, chapter_id) "
                "VALUES (:tid, :uid, :cid) ON CONFLICT DO NOTHING"
            ),
            {"tid": twist_id, "uid": users[i], "cid": chapter_id},
        )


async def _cleanup(
    session: AsyncSession, season_id: int, *user_pairs: tuple[int, str]
) -> None:
    await session.execute(
        sa.text("DELETE FROM seasons WHERE id = :id"), {"id": season_id}
    )
    for uid, code in user_pairs:
        await session.execute(
            sa.text("DELETE FROM users WHERE id = :id"), {"id": uid}
        )
        await session.execute(
            sa.text("DELETE FROM invites WHERE code = :code"), {"code": code}
        )
    await session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_winner(session: AsyncSession) -> None:
    """Twist with most votes wins; tiebreak=False, runner_up=None."""
    uid1, c1 = await _make_user(session, "Alice")
    uid2, c2 = await _make_user(session, "Bob")
    uid3, c3 = await _make_user(session, "Carol")
    sid, chapter_id = await _make_season_chapter(session, "clear")

    t_leader = await _make_twist(session, chapter_id, uid1, "Twist winner")
    t_second = await _make_twist(session, chapter_id, uid2, "Twist loser")

    await _cast_votes(session, t_leader, chapter_id, 3, [uid1, uid2, uid3])
    await _cast_votes(session, t_second, chapter_id, 1, [uid3])
    await session.flush()

    result = await pick_winner(session, chapter_id)

    try:
        assert result.winner_twist_id == t_leader
        assert isinstance(result.winner_public_id, UUID)
        assert result.winner_user_display_name == "Alice"
        assert result.vote_count == 3
        assert result.tiebreak is False
        assert result.runner_up_twist_id is None
    finally:
        await _cleanup(session, sid, (uid1, c1), (uid2, c2), (uid3, c3))


@pytest.mark.asyncio
async def test_two_way_tie_earlier_submitted_at_wins(session: AsyncSession) -> None:
    """Equal vote_count → older twist wins; tiebreak=True, runner_up populated."""
    uid1, c1 = await _make_user(session, "Dave")
    uid2, c2 = await _make_user(session, "Eve")
    uid3, c3 = await _make_user(session, "Frank")
    sid, chapter_id = await _make_season_chapter(session, "tie2")

    base = datetime(2026, 6, 14, 10, 0, 0, tzinfo=UTC)
    t_older = await _make_twist(
        session, chapter_id, uid1, "Older twist", submitted_at=base
    )
    t_newer = await _make_twist(
        session, chapter_id, uid2, "Newer twist", submitted_at=base + timedelta(hours=1)
    )

    # Both get 2 votes
    await _cast_votes(session, t_older, chapter_id, 2, [uid2, uid3])
    await _cast_votes(session, t_newer, chapter_id, 2, [uid1, uid3])
    await session.flush()

    result = await pick_winner(session, chapter_id)

    try:
        assert result.winner_twist_id == t_older
        assert result.winner_user_display_name == "Dave"
        assert result.vote_count == 2
        assert result.tiebreak is True
        assert result.runner_up_twist_id is not None
        assert isinstance(result.runner_up_twist_id, UUID)
    finally:
        await _cleanup(session, sid, (uid1, c1), (uid2, c2), (uid3, c3))


@pytest.mark.asyncio
async def test_three_way_tie_lower_id_wins(session: AsyncSession) -> None:
    """Equal vote_count + equal submitted_at → lowest internal id wins."""
    uid1, c1 = await _make_user(session, "Grace")
    uid2, c2 = await _make_user(session, "Hank")
    uid3, c3 = await _make_user(session, "Iris")
    uid4, c4 = await _make_user(session, "Jack")
    sid, chapter_id = await _make_season_chapter(session, "tie3")

    # All 3 twists same submitted_at (server default NOW() within same transaction)
    ts = datetime(2026, 6, 14, 11, 0, 0, tzinfo=UTC)
    t1 = await _make_twist(session, chapter_id, uid1, "Twist A", submitted_at=ts)
    t2 = await _make_twist(session, chapter_id, uid2, "Twist B", submitted_at=ts)
    t3 = await _make_twist(session, chapter_id, uid3, "Twist C", submitted_at=ts)

    # All 3 get exactly 1 vote
    await _cast_votes(session, t1, chapter_id, 1, [uid4])
    await _cast_votes(session, t2, chapter_id, 1, [uid3])
    await _cast_votes(session, t3, chapter_id, 1, [uid2])
    await session.flush()

    result = await pick_winner(session, chapter_id)

    try:
        # Lowest id (t1) wins; runner_up is t2 (next lowest id)
        assert result.winner_twist_id == t1
        assert result.vote_count == 1
        assert result.tiebreak is True
        assert result.runner_up_twist_id is not None
    finally:
        await _cleanup(
            session, sid, (uid1, c1), (uid2, c2), (uid3, c3), (uid4, c4)
        )


@pytest.mark.asyncio
async def test_zero_approved_twists_returns_null_pick(session: AsyncSession) -> None:
    """No approved twists → null WinnerPick for auto-continue mode."""
    uid1, c1 = await _make_user(session, "Karen")
    sid, chapter_id = await _make_season_chapter(session, "zero")

    # Insert a rejected twist (must not influence result)
    await session.execute(
        sa.text(
            "INSERT INTO twists (chapter_id, user_id, content, status) "
            "VALUES (:cid, :uid, 'Rejected content', 'rejected_spam')"
        ),
        {"cid": chapter_id, "uid": uid1},
    )
    await session.flush()

    result = await pick_winner(session, chapter_id)

    try:
        assert result == WinnerPick(
            winner_twist_id=None,
            winner_public_id=None,
            winner_user_display_name=None,
            vote_count=0,
            tiebreak=False,
            runner_up_twist_id=None,
        )
    finally:
        await _cleanup(session, sid, (uid1, c1))
