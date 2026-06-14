"""Integration tests: VotesRepo.

Module 007 / Task T-004.

Skips when DATABASE_URL is the conftest placeholder.
Each test creates fresh season + chapter + user(s) + twist(s) and cleans
up in ``finally`` blocks. Votes CASCADE with their twist; twists CASCADE
with the chapter; chapters CASCADE with the season.

Coverage:
  - ``count_for_user_chapter`` zero / non-zero / isolated per user.
  - ``count_for_twist`` zero / non-zero.
  - ``list_for_user_chapter`` order + isolation.
  - ``vote_atomic`` returns id on first call, None on duplicate.
  - ``lock_user_chapter`` happy path.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.infra.votes_repo import Vote, VotesRepo

API_DIR = Path(__file__).parent.parent.parent
ALEMBIC_INI = API_DIR / "alembic.ini"

_SLUG_PREFIX = "_vr-test-"
_TODAY = date(2026, 6, 13)
_INVITE_ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def _alembic_config(database_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


@pytest.fixture(scope="module")
def database_url() -> str:
    from tests.conftest import _is_placeholder_database_url

    url = os.environ.get("DATABASE_URL", "")
    if not url or _is_placeholder_database_url(url):
        pytest.skip("DATABASE_URL no apunta a una base real.")
    return url


@pytest.fixture(scope="module", autouse=True)
def _ensure_migrated(database_url: str) -> None:
    cfg = _alembic_config(database_url)
    asyncio.get_event_loop().run_until_complete(
        asyncio.to_thread(command.upgrade, cfg, "head")
    )


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
# Helpers — fresh season + chapter + user + twist for each test
# ---------------------------------------------------------------------------


def _fresh_invite_code() -> str:
    """Generate an invite code matching ``ck_invites_code_format``."""
    left = "".join(secrets.choice(_INVITE_ALPHA) for _ in range(4))
    right = "".join(secrets.choice(_INVITE_ALPHA) for _ in range(4))
    return f"{left}-{right}"


async def _make_user(session: AsyncSession) -> tuple[int, str]:
    code = _fresh_invite_code()
    expires_at = datetime.now(UTC) + timedelta(days=7)
    await session.execute(
        sa.text(
            "INSERT INTO invites (code, issued_by, expires_at, status) "
            "VALUES (:code, 'test', :expires_at, 'unused')"
        ),
        {"code": code, "expires_at": expires_at},
    )
    result = await session.execute(
        sa.text(
            "INSERT INTO users (display_name, invite_code, device_token) "
            "VALUES ('TmpUser', :code, :token) "
            "RETURNING id"
        ),
        {"code": code, "token": (uuid4().hex * 2)[:64]},
    )
    return int(result.scalar_one()), code


async def _make_season_and_chapter(
    session: AsyncSession, suffix: str
) -> tuple[int, int]:
    result = await session.execute(
        sa.text(
            "INSERT INTO seasons (slug, title, bible_json, started_on, is_active) "
            "VALUES (:slug, 'VR Test Season', '{}', :today, FALSE) "
            "RETURNING id"
        ),
        {"slug": f"{_SLUG_PREFIX}{suffix}", "today": _TODAY},
    )
    season_id = int(result.scalar_one())

    result = await session.execute(
        sa.text(
            "INSERT INTO chapters "
            "(season_id, day_index, title, synopsis, manifest_json, status) "
            "VALUES (:sid, 1, 'Chap', 'Syn', '{}', 'live') "
            "RETURNING id"
        ),
        {"sid": season_id},
    )
    chapter_id = int(result.scalar_one())
    return season_id, chapter_id


async def _make_twist(
    session: AsyncSession, chapter_id: int, user_id: int, content: str
) -> int:
    """Insert an approved twist directly, returning its id."""
    result = await session.execute(
        sa.text(
            "INSERT INTO twists (chapter_id, user_id, content, status) "
            "VALUES (:cid, :uid, :content, 'approved') "
            "RETURNING id"
        ),
        {"cid": chapter_id, "uid": user_id, "content": content},
    )
    return int(result.scalar_one())


async def _cleanup(
    session: AsyncSession,
    season_id: int,
    *users: tuple[int, str],
) -> None:
    """Delete fixtures in dependency order.

    Season delete cascades chapters → twists → votes. Users must be
    deleted before their invites (FK).
    """
    await session.execute(
        sa.text("DELETE FROM seasons WHERE id = :id"), {"id": season_id}
    )
    for uid, code in users:
        await session.execute(
            sa.text("DELETE FROM users WHERE id = :id"), {"id": uid}
        )
        await session.execute(
            sa.text("DELETE FROM invites WHERE code = :code"), {"code": code}
        )
    await session.commit()


# ---------------------------------------------------------------------------
# count_for_user_chapter
# ---------------------------------------------------------------------------


async def test_count_zero_for_no_votes(session: AsyncSession) -> None:
    season_id, chapter_id = await _make_season_and_chapter(session, "cnt-zero")
    user = await _make_user(session)
    await session.commit()
    repo = VotesRepo(session)
    try:
        assert await repo.count_for_user_chapter(user[0], chapter_id) == 0
    finally:
        await _cleanup(session, season_id, user)


async def test_count_isolated_per_user(session: AsyncSession) -> None:
    season_id, chapter_id = await _make_season_and_chapter(session, "cnt-iso")
    u1 = await _make_user(session)
    u2 = await _make_user(session)
    twist_id = await _make_twist(session, chapter_id, u1[0], "compartido xxx")
    await session.commit()
    repo = VotesRepo(session)
    try:
        # u1 casts a vote; u2 does not
        new_id = await repo.vote_atomic(twist_id, u1[0], chapter_id)
        assert new_id is not None
        await session.commit()

        assert await repo.count_for_user_chapter(u1[0], chapter_id) == 1
        assert await repo.count_for_user_chapter(u2[0], chapter_id) == 0
    finally:
        await _cleanup(session, season_id, u1, u2)


# ---------------------------------------------------------------------------
# count_for_twist
# ---------------------------------------------------------------------------


async def test_count_for_twist_zero_then_one(session: AsyncSession) -> None:
    season_id, chapter_id = await _make_season_and_chapter(session, "cft-001")
    voter = await _make_user(session)
    author = await _make_user(session)
    twist_id = await _make_twist(session, chapter_id, author[0], "idea unica xxx")
    await session.commit()
    repo = VotesRepo(session)
    try:
        assert await repo.count_for_twist(twist_id) == 0
        await repo.vote_atomic(twist_id, voter[0], chapter_id)
        await session.commit()
        assert await repo.count_for_twist(twist_id) == 1
    finally:
        await _cleanup(session, season_id, voter, author)


# ---------------------------------------------------------------------------
# list_for_user_chapter
# ---------------------------------------------------------------------------


async def test_list_ordered_by_created_at_asc(session: AsyncSession) -> None:
    season_id, chapter_id = await _make_season_and_chapter(session, "list-ord")
    user = await _make_user(session)
    t1 = await _make_twist(session, chapter_id, user[0], "twist 1 xxxx xxx")
    t2 = await _make_twist(session, chapter_id, user[0], "twist 2 xxxx xxx")
    t3 = await _make_twist(session, chapter_id, user[0], "twist 3 xxxx xxx")
    await session.commit()
    repo = VotesRepo(session)
    try:
        v1 = await repo.vote_atomic(t1, user[0], chapter_id)
        await session.commit()
        v2 = await repo.vote_atomic(t2, user[0], chapter_id)
        await session.commit()
        v3 = await repo.vote_atomic(t3, user[0], chapter_id)
        await session.commit()
        assert v1 is not None and v2 is not None and v3 is not None

        votes = await repo.list_for_user_chapter(user[0], chapter_id)
        assert [v.id for v in votes] == [v1, v2, v3]
        assert all(isinstance(v, Vote) for v in votes)
    finally:
        await _cleanup(session, season_id, user)


async def test_list_excludes_other_users(session: AsyncSession) -> None:
    season_id, chapter_id = await _make_season_and_chapter(session, "list-iso")
    u1 = await _make_user(session)
    u2 = await _make_user(session)
    t = await _make_twist(session, chapter_id, u1[0], "compartido xxx xxx")
    await session.commit()
    repo = VotesRepo(session)
    try:
        await repo.vote_atomic(t, u1[0], chapter_id)
        await repo.vote_atomic(t, u2[0], chapter_id)
        await session.commit()

        u1_votes = await repo.list_for_user_chapter(u1[0], chapter_id)
        u2_votes = await repo.list_for_user_chapter(u2[0], chapter_id)
        assert len(u1_votes) == 1 and u1_votes[0].user_id == u1[0]
        assert len(u2_votes) == 1 and u2_votes[0].user_id == u2[0]
    finally:
        await _cleanup(session, season_id, u1, u2)


# ---------------------------------------------------------------------------
# vote_atomic
# ---------------------------------------------------------------------------


async def test_vote_atomic_returns_id_on_first_insert(
    session: AsyncSession,
) -> None:
    season_id, chapter_id = await _make_season_and_chapter(session, "va-first")
    user = await _make_user(session)
    twist_id = await _make_twist(session, chapter_id, user[0], "votala xxxx xxx")
    await session.commit()
    repo = VotesRepo(session)
    try:
        row_id = await repo.vote_atomic(twist_id, user[0], chapter_id)
        await session.commit()
        assert row_id is not None
        assert row_id > 0
    finally:
        await _cleanup(session, season_id, user)


async def test_vote_atomic_returns_none_on_duplicate(
    session: AsyncSession,
) -> None:
    """Same (twist_id, user_id) twice → second call returns None (UNIQUE absorbs)."""
    season_id, chapter_id = await _make_season_and_chapter(session, "va-dup")
    user = await _make_user(session)
    twist_id = await _make_twist(session, chapter_id, user[0], "duplicada xx xx")
    await session.commit()
    repo = VotesRepo(session)
    try:
        first = await repo.vote_atomic(twist_id, user[0], chapter_id)
        await session.commit()
        second = await repo.vote_atomic(twist_id, user[0], chapter_id)
        await session.commit()
        assert first is not None
        assert second is None
        # And only one row exists
        assert await repo.count_for_twist(twist_id) == 1
    finally:
        await _cleanup(session, season_id, user)


# ---------------------------------------------------------------------------
# lock_user_chapter
# ---------------------------------------------------------------------------


async def test_lock_user_chapter_happy_path(session: AsyncSession) -> None:
    """Lock acquires inside an open transaction without raising."""
    season_id, chapter_id = await _make_season_and_chapter(session, "lock-ok")
    user = await _make_user(session)
    await session.commit()
    repo = VotesRepo(session)
    try:
        # Inside a fresh transaction
        await session.begin()
        try:
            await repo.lock_user_chapter(user[0], chapter_id)
            # If we got here, no exception was raised
        finally:
            await session.rollback()
    finally:
        await _cleanup(session, season_id, user)
