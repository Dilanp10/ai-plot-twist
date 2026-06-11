"""Integration tests: TwistsRepo.

Module 005 / Task T-004.

Skips when DATABASE_URL is the conftest placeholder.
Each test creates fresh season + chapter + user(s) and cleans up in
``finally`` blocks. Twists CASCADE with their chapter.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.infra.twists_repo import Twist, TwistsRepo

API_DIR = Path(__file__).parent.parent.parent
ALEMBIC_INI = API_DIR / "alembic.ini"

_SLUG_PREFIX = "_tr-test-"
_TODAY = date(2026, 6, 11)
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
# Helpers
# ---------------------------------------------------------------------------


def _fresh_invite_code() -> str:
    """Generate an invite code matching ``ck_invites_code_format``."""
    left = "".join(secrets.choice(_INVITE_ALPHA) for _ in range(4))
    right = "".join(secrets.choice(_INVITE_ALPHA) for _ in range(4))
    return f"{left}-{right}"


async def _make_user(session: AsyncSession) -> tuple[int, str]:
    """Insert a fresh invite + user; return ``(user_id, invite_code)``.

    The invite is required by the ``users.invite_code`` FK to ``invites.code``.
    Cleanup must delete the user before the invite (FK constraint).
    """
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
    """Insert a minimal inactive season + a single live chapter."""
    result = await session.execute(
        sa.text(
            "INSERT INTO seasons (slug, title, bible_json, started_on, is_active) "
            "VALUES (:slug, 'TR Test Season', '{}', :today, FALSE) "
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


async def _cleanup(
    session: AsyncSession,
    season_id: int,
    *users: tuple[int, str],
) -> None:
    """Delete fixtures in dependency order.

    Season delete cascades chapters/cycles → twists also cascade with
    chapter. Users must be deleted before their invites (FK).
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


async def test_count_zero_for_no_twists(session: AsyncSession) -> None:
    season_id, chapter_id = await _make_season_and_chapter(session, "cnt-zero")
    user = await _make_user(session)
    await session.commit()
    repo = TwistsRepo(session)
    try:
        assert await repo.count_for_user_chapter(user[0], chapter_id) == 0
    finally:
        await _cleanup(session, season_id, user)


async def test_count_includes_deleted_status(session: AsyncSession) -> None:
    """FR-004: deleted twists count toward the quota too."""
    season_id, chapter_id = await _make_season_and_chapter(session, "cnt-all")
    user = await _make_user(session)
    await session.commit()
    repo = TwistsRepo(session)
    try:
        await repo.insert(chapter_id, user[0], "uno uno uno uno")
        t2 = await repo.insert(chapter_id, user[0], "dos dos dos dos")
        await repo.insert(chapter_id, user[0], "tres tres tres tres")
        await session.commit()
        await repo.soft_delete(t2.id)
        await session.commit()

        assert await repo.count_for_user_chapter(user[0], chapter_id) == 3
    finally:
        await _cleanup(session, season_id, user)


async def test_count_isolated_per_user(session: AsyncSession) -> None:
    season_id, chapter_id = await _make_season_and_chapter(session, "cnt-iso")
    u1 = await _make_user(session)
    u2 = await _make_user(session)
    await session.commit()
    repo = TwistsRepo(session)
    try:
        await repo.insert(chapter_id, u1[0], "user 1 twist xxxx")
        await repo.insert(chapter_id, u1[0], "user 1 twist yyyy")
        await repo.insert(chapter_id, u2[0], "user 2 twist zzzz")
        await session.commit()

        assert await repo.count_for_user_chapter(u1[0], chapter_id) == 2
        assert await repo.count_for_user_chapter(u2[0], chapter_id) == 1
    finally:
        await _cleanup(session, season_id, u1, u2)


# ---------------------------------------------------------------------------
# insert
# ---------------------------------------------------------------------------


async def test_insert_returns_full_twist(session: AsyncSession) -> None:
    season_id, chapter_id = await _make_season_and_chapter(session, "ins-001")
    user = await _make_user(session)
    await session.commit()
    repo = TwistsRepo(session)
    try:
        twist = await repo.insert(chapter_id, user[0], "Mi idea brillante")
        await session.commit()

        assert isinstance(twist, Twist)
        assert twist.id > 0
        assert isinstance(twist.public_id, UUID)
        assert twist.chapter_id == chapter_id
        assert twist.user_id == user[0]
        assert twist.content == "Mi idea brillante"
        assert twist.status == "pending_review"
        assert isinstance(twist.submitted_at, datetime)
        assert twist.reviewed_at is None
        assert twist.deleted_at is None
        assert twist.director_reason is None
    finally:
        await _cleanup(session, season_id, user)


# ---------------------------------------------------------------------------
# get_by_public_id_for_update
# ---------------------------------------------------------------------------


async def test_get_by_public_id_returns_twist(session: AsyncSession) -> None:
    season_id, chapter_id = await _make_season_and_chapter(session, "gbi-001")
    user = await _make_user(session)
    await session.commit()
    repo = TwistsRepo(session)
    try:
        inserted = await repo.insert(chapter_id, user[0], "Busca esta idea")
        await session.commit()

        fetched = await repo.get_by_public_id_for_update(inserted.public_id)
        assert fetched is not None
        assert fetched.id == inserted.id
        assert fetched.public_id == inserted.public_id
        assert fetched.content == "Busca esta idea"
    finally:
        await _cleanup(session, season_id, user)


async def test_get_by_public_id_returns_none_for_missing(
    session: AsyncSession,
) -> None:
    repo = TwistsRepo(session)
    assert await repo.get_by_public_id_for_update(uuid4()) is None


# ---------------------------------------------------------------------------
# soft_delete
# ---------------------------------------------------------------------------


async def test_soft_delete_persists_status_and_returns_timestamp(
    session: AsyncSession,
) -> None:
    season_id, chapter_id = await _make_season_and_chapter(session, "del-001")
    user = await _make_user(session)
    await session.commit()
    repo = TwistsRepo(session)
    try:
        inserted = await repo.insert(chapter_id, user[0], "Para borrar luego")
        await session.commit()

        deleted_at = await repo.soft_delete(inserted.id)
        await session.commit()
        assert isinstance(deleted_at, datetime)

        fetched = await repo.get_by_public_id_for_update(inserted.public_id)
        assert fetched is not None
        assert fetched.status == "deleted_by_user"
        assert fetched.deleted_at == deleted_at
    finally:
        await _cleanup(session, season_id, user)


# ---------------------------------------------------------------------------
# list_for_user_chapter
# ---------------------------------------------------------------------------


async def test_list_ordered_by_submitted_at_asc(session: AsyncSession) -> None:
    season_id, chapter_id = await _make_season_and_chapter(session, "list-ord")
    user = await _make_user(session)
    await session.commit()
    repo = TwistsRepo(session)
    try:
        t1 = await repo.insert(chapter_id, user[0], "primera idea xxxx")
        await session.commit()
        t2 = await repo.insert(chapter_id, user[0], "segunda idea xxxx")
        await session.commit()
        t3 = await repo.insert(chapter_id, user[0], "tercera idea xxxx")
        await session.commit()

        twists = await repo.list_for_user_chapter(user[0], chapter_id, limit=10)
        assert [t.id for t in twists] == [t1.id, t2.id, t3.id]
    finally:
        await _cleanup(session, season_id, user)


async def test_list_respects_limit(session: AsyncSession) -> None:
    season_id, chapter_id = await _make_season_and_chapter(session, "list-lim")
    user = await _make_user(session)
    await session.commit()
    repo = TwistsRepo(session)
    try:
        for i in range(5):
            await repo.insert(chapter_id, user[0], f"idea {i} xxxxxxxx")
            await session.commit()

        twists = await repo.list_for_user_chapter(user[0], chapter_id, limit=3)
        assert len(twists) == 3
    finally:
        await _cleanup(session, season_id, user)


async def test_list_empty_for_user_without_twists(
    session: AsyncSession,
) -> None:
    season_id, chapter_id = await _make_season_and_chapter(session, "list-empty")
    user = await _make_user(session)
    await session.commit()
    repo = TwistsRepo(session)
    try:
        twists = await repo.list_for_user_chapter(user[0], chapter_id, limit=10)
        assert twists == []
    finally:
        await _cleanup(session, season_id, user)


# ---------------------------------------------------------------------------
# lock_user_chapter
# ---------------------------------------------------------------------------


async def test_lock_user_chapter_smoke(session: AsyncSession) -> None:
    """Smoke: acquiring the lock on a fresh session does not raise.

    Real race contention is verified in T-010 (concurrent submits).
    """
    season_id, chapter_id = await _make_season_and_chapter(session, "lock-smk")
    user = await _make_user(session)
    await session.commit()
    repo = TwistsRepo(session)
    try:
        await repo.lock_user_chapter(user[0], chapter_id)
        # Re-acquire in the same transaction is a no-op (advisory locks
        # are reentrant within the same backend).
        await repo.lock_user_chapter(user[0], chapter_id)
    finally:
        await session.rollback()
        await _cleanup(session, season_id, user)
