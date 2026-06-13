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
from app.infra.twists_repo import Twist, TwistsRepo, VerdictUpdate

API_DIR = Path(__file__).parent.parent.parent
ALEMBIC_INI = API_DIR / "alembic.ini"

_SLUG_PREFIX = "_twr-test-"
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


# ---------------------------------------------------------------------------
# list_pending_for_chapter  (module 006 / T-008)
# ---------------------------------------------------------------------------


async def _force_status(
    session: AsyncSession, twist_id: int, status: str, reason: str | None = None
) -> None:
    """Bypass the repo to put a twist in an arbitrary classified status.

    Used to seed mixed-status fixtures for the filter/replay tests.
    """
    await session.execute(
        sa.text(
            "UPDATE twists "
            "SET status = :s, director_reason = :r, reviewed_at = now() "
            "WHERE id = :id"
        ),
        {"s": status, "r": reason, "id": twist_id},
    )


async def test_list_pending_for_chapter_orders_by_submitted_at_asc(
    session: AsyncSession,
) -> None:
    season_id, chapter_id = await _make_season_and_chapter(session, "pend-ord")
    user = await _make_user(session)
    await session.commit()
    repo = TwistsRepo(session)
    try:
        t1 = await repo.insert(chapter_id, user[0], "primera pending xxx")
        await session.commit()
        t2 = await repo.insert(chapter_id, user[0], "segunda pending xxx")
        await session.commit()
        t3 = await repo.insert(chapter_id, user[0], "tercera pending xxx")
        await session.commit()

        rows = await repo.list_pending_for_chapter(chapter_id)
        assert [t.id for t in rows] == [t1.id, t2.id, t3.id]
        assert all(t.status == "pending_review" for t in rows)
    finally:
        await _cleanup(session, season_id, user)


async def test_list_pending_for_chapter_excludes_non_pending(
    session: AsyncSession,
) -> None:
    """Only ``pending_review`` rows come back — approved/rejected/deleted out."""
    season_id, chapter_id = await _make_season_and_chapter(session, "pend-excl")
    user = await _make_user(session)
    await session.commit()
    repo = TwistsRepo(session)
    try:
        t_pending = await repo.insert(chapter_id, user[0], "queda pendiente xxx")
        t_approved = await repo.insert(chapter_id, user[0], "lo aprueban xxxxxx")
        t_rejected = await repo.insert(chapter_id, user[0], "lo rechazan xxxxxx")
        t_deleted = await repo.insert(chapter_id, user[0], "lo borran xxxxxxxx")
        await session.commit()

        await _force_status(session, t_approved.id, "approved", "ok")
        await _force_status(
            session, t_rejected.id, "rejected_incoherent", "off-topic"
        )
        await session.commit()
        await repo.soft_delete(t_deleted.id)
        await session.commit()

        rows = await repo.list_pending_for_chapter(chapter_id)
        assert [t.id for t in rows] == [t_pending.id]
    finally:
        await _cleanup(session, season_id, user)


async def test_list_pending_for_chapter_empty_returns_empty_list(
    session: AsyncSession,
) -> None:
    season_id, chapter_id = await _make_season_and_chapter(session, "pend-empty")
    user = await _make_user(session)
    await session.commit()
    repo = TwistsRepo(session)
    try:
        assert await repo.list_pending_for_chapter(chapter_id) == []
    finally:
        await _cleanup(session, season_id, user)


# ---------------------------------------------------------------------------
# list_all_for_chapter_for_replay  (module 006 / T-008)
# ---------------------------------------------------------------------------


async def test_list_replay_includes_classified_excludes_deleted(
    session: AsyncSession,
) -> None:
    season_id, chapter_id = await _make_season_and_chapter(session, "rep-mix")
    user = await _make_user(session)
    await session.commit()
    repo = TwistsRepo(session)
    try:
        t_pending = await repo.insert(chapter_id, user[0], "sigue pending xxxx")
        t_approved = await repo.insert(chapter_id, user[0], "ya aprobado xxxxx")
        t_rejected = await repo.insert(chapter_id, user[0], "ya rechazado xxxx")
        t_deleted = await repo.insert(chapter_id, user[0], "el borrado xxxxxxx")
        await session.commit()

        await _force_status(session, t_approved.id, "approved", "ok")
        await _force_status(
            session, t_rejected.id, "rejected_offensive", "slur"
        )
        await session.commit()
        await repo.soft_delete(t_deleted.id)
        await session.commit()

        rows = await repo.list_all_for_chapter_for_replay(chapter_id)
        ids = {t.id for t in rows}
        assert ids == {t_pending.id, t_approved.id, t_rejected.id}
        # Order is ASC by submitted_at — three inserts in sequence.
        assert [t.id for t in rows] == [
            t_pending.id,
            t_approved.id,
            t_rejected.id,
        ]
    finally:
        await _cleanup(session, season_id, user)


# ---------------------------------------------------------------------------
# update_status_bulk  (module 006 / T-008)
# ---------------------------------------------------------------------------


async def test_update_status_bulk_empty_is_noop(session: AsyncSession) -> None:
    repo = TwistsRepo(session)
    assert await repo.update_status_bulk([]) == 0
    assert await repo.update_status_bulk([], allow_already_classified=True) == 0


async def test_update_status_bulk_strict_updates_pending(
    session: AsyncSession,
) -> None:
    """Default mode writes status+reason+reviewed_at on pending twists."""
    season_id, chapter_id = await _make_season_and_chapter(session, "upd-ok")
    user = await _make_user(session)
    await session.commit()
    repo = TwistsRepo(session)
    try:
        t1 = await repo.insert(chapter_id, user[0], "primero pending xxxx")
        t2 = await repo.insert(chapter_id, user[0], "segundo pending xxxx")
        await session.commit()

        affected = await repo.update_status_bulk(
            [
                VerdictUpdate(t1.id, "approved", "todo bien"),
                VerdictUpdate(t2.id, "rejected_spam", "publicidad"),
            ]
        )
        await session.commit()
        assert affected == 2

        fetched1 = await repo.get_by_public_id_for_update(t1.public_id)
        fetched2 = await repo.get_by_public_id_for_update(t2.public_id)
        assert fetched1 is not None and fetched2 is not None
        assert fetched1.status == "approved"
        assert fetched1.director_reason == "todo bien"
        assert fetched1.reviewed_at is not None
        assert fetched2.status == "rejected_spam"
        assert fetched2.director_reason == "publicidad"
        assert fetched2.reviewed_at is not None
    finally:
        await _cleanup(session, season_id, user)


async def test_update_status_bulk_strict_skips_already_classified(
    session: AsyncSession,
) -> None:
    """Strict guard means re-running a verdict for an approved twist is a no-op."""
    season_id, chapter_id = await _make_season_and_chapter(session, "upd-skip")
    user = await _make_user(session)
    await session.commit()
    repo = TwistsRepo(session)
    try:
        t_pending = await repo.insert(chapter_id, user[0], "queda pending xxxx")
        t_approved = await repo.insert(chapter_id, user[0], "ya aprobado xxxxx")
        await session.commit()

        await _force_status(session, t_approved.id, "approved", "old reason")
        await session.commit()

        affected = await repo.update_status_bulk(
            [
                VerdictUpdate(t_pending.id, "approved", "fresh"),
                VerdictUpdate(t_approved.id, "rejected_spam", "should not stick"),
            ]
        )
        await session.commit()
        assert affected == 1

        fetched_approved = await repo.get_by_public_id_for_update(
            t_approved.public_id
        )
        assert fetched_approved is not None
        assert fetched_approved.status == "approved"
        assert fetched_approved.director_reason == "old reason"
    finally:
        await _cleanup(session, season_id, user)


async def test_update_status_bulk_relaxed_overwrites_classified(
    session: AsyncSession,
) -> None:
    """Replay mode rewrites already-classified twists."""
    season_id, chapter_id = await _make_season_and_chapter(session, "upd-replay")
    user = await _make_user(session)
    await session.commit()
    repo = TwistsRepo(session)
    try:
        t_approved = await repo.insert(chapter_id, user[0], "lo re-clasifican xx")
        await session.commit()

        await _force_status(session, t_approved.id, "approved", "previous")
        await session.commit()

        affected = await repo.update_status_bulk(
            [VerdictUpdate(t_approved.id, "rejected_offensive", "post-filter")],
            allow_already_classified=True,
        )
        await session.commit()
        assert affected == 1

        fetched = await repo.get_by_public_id_for_update(t_approved.public_id)
        assert fetched is not None
        assert fetched.status == "rejected_offensive"
        assert fetched.director_reason == "post-filter"
    finally:
        await _cleanup(session, season_id, user)


async def test_update_status_bulk_relaxed_still_skips_deleted(
    session: AsyncSession,
) -> None:
    """Even in replay mode, deleted_by_user is sacred and never re-classified."""
    season_id, chapter_id = await _make_season_and_chapter(session, "upd-del")
    user = await _make_user(session)
    await session.commit()
    repo = TwistsRepo(session)
    try:
        t_del = await repo.insert(chapter_id, user[0], "se lo borra xxxxxxx")
        await session.commit()
        await repo.soft_delete(t_del.id)
        await session.commit()

        affected = await repo.update_status_bulk(
            [VerdictUpdate(t_del.id, "approved", "should not touch")],
            allow_already_classified=True,
        )
        await session.commit()
        assert affected == 0

        fetched = await repo.get_by_public_id_for_update(t_del.public_id)
        assert fetched is not None
        assert fetched.status == "deleted_by_user"
        assert fetched.director_reason is None
    finally:
        await _cleanup(session, season_id, user)
