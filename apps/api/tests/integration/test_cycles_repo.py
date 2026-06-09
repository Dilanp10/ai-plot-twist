"""Integration tests: CyclesRepo.

Module 003 / Task T-009.

Skips when DATABASE_URL is the conftest placeholder (no real DB available).
Each test creates its own season + chapter + cycle and deletes them in
finally blocks.  Seasons inserted here are INACTIVE to avoid conflicting with
the uniq_one_active_season constraint, except where get_active() is tested.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.infra.cycles_repo import CycleRow, CyclesRepo, LockBusy

API_DIR = Path(__file__).parent.parent.parent
ALEMBIC_INI = API_DIR / "alembic.ini"

_SLUG_PREFIX = "_cy-test-"
_TODAY = date(2026, 6, 9)


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
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            asyncio.to_thread(command.upgrade, _alembic_config(database_url), "head")
        )
    finally:
        loop.close()


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


async def _make_season(session: AsyncSession, suffix: str, *, active: bool) -> int:
    """Insert a test season and return its id."""
    result = await session.execute(
        sa.text(
            "INSERT INTO seasons (slug, title, bible_json, started_on, is_active) "
            "VALUES (:slug, 'CY Test', '{}', :today, :active) "
            "RETURNING id"
        ),
        {
            "slug": f"{_SLUG_PREFIX}{suffix}",
            "today": _TODAY,
            "active": active,
        },
    )
    return int(result.scalar_one())


async def _make_chapter(session: AsyncSession, season_id: int) -> int:
    result = await session.execute(
        sa.text(
            "INSERT INTO chapters "
            "(season_id, day_index, title, synopsis, manifest_json, status) "
            "VALUES (:sid, 1, 'T', 'S', '{}', 'ready') "
            "RETURNING id"
        ),
        {"sid": season_id},
    )
    return int(result.scalar_one())


async def _make_cycle(
    session: AsyncSession,
    season_id: int,
    chapter_id: int,
    cycle_date: date = _TODAY,
) -> int:
    repo = CyclesRepo(session)
    return await repo.insert(
        season_id=season_id,
        chapter_id=chapter_id,
        cycle_date=cycle_date,
    )


async def _cleanup_season(session: AsyncSession, season_id: int) -> None:
    await session.execute(
        sa.text("DELETE FROM seasons WHERE id = :id"), {"id": season_id}
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Tests — insert
# ---------------------------------------------------------------------------


async def test_insert_returns_int_id(session: AsyncSession) -> None:
    season_id = await _make_season(session, "ins-001", active=False)
    chapter_id = await _make_chapter(session, season_id)
    await session.commit()
    try:
        cycle_id = await _make_cycle(session, season_id, chapter_id)
        await session.commit()
        assert isinstance(cycle_id, int)
        assert cycle_id > 0
    finally:
        await _cleanup_season(session, season_id)


async def test_insert_initial_state_is_pending_release(session: AsyncSession) -> None:
    season_id = await _make_season(session, "ins-state-001", active=False)
    chapter_id = await _make_chapter(session, season_id)
    await session.commit()
    try:
        cycle_id = await _make_cycle(session, season_id, chapter_id)
        await session.commit()

        row = await session.execute(
            sa.text("SELECT state FROM cycles WHERE id = :id"), {"id": cycle_id}
        )
        state = row.scalar_one()
        assert state == "PENDING_RELEASE"
    finally:
        await _cleanup_season(session, season_id)


# ---------------------------------------------------------------------------
# Tests — get_active
# ---------------------------------------------------------------------------


async def test_get_active_returns_cycle_for_active_season(
    session: AsyncSession,
) -> None:
    # Deactivate any existing active season first.
    await session.execute(
        sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE")
    )
    await session.commit()

    season_id = await _make_season(session, "getact-001", active=True)
    chapter_id = await _make_chapter(session, season_id)
    await session.commit()
    try:
        cycle_id = await _make_cycle(session, season_id, chapter_id)
        await session.commit()

        repo = CyclesRepo(session)
        cycle = await repo.get_active()
        assert cycle is not None
        assert isinstance(cycle, CycleRow)
        assert cycle.id == cycle_id
        assert cycle.season_id == season_id
        assert cycle.chapter_id == chapter_id
        assert cycle.next_chapter_id is None
        assert cycle.state == "PENDING_RELEASE"
        assert cycle.cycle_date == _TODAY
    finally:
        await _cleanup_season(session, season_id)


async def test_get_active_returns_none_when_no_active_season(
    session: AsyncSession,
) -> None:
    await session.execute(
        sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE")
    )
    await session.commit()
    repo = CyclesRepo(session)
    result = await repo.get_active()
    assert result is None


async def test_get_active_returns_latest_cycle_by_date(
    session: AsyncSession,
) -> None:
    """When multiple cycles exist, get_active returns the most recent by date."""
    await session.execute(
        sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE")
    )
    await session.commit()

    season_id = await _make_season(session, "getact-multi-001", active=True)
    chapter_id = await _make_chapter(session, season_id)
    await session.commit()
    try:
        # Insert older cycle first, then newer.
        older_date = date(2026, 6, 8)
        newer_date = date(2026, 6, 9)
        await _make_cycle(session, season_id, chapter_id, older_date)
        newer_id = await _make_cycle(session, season_id, chapter_id, newer_date)
        await session.commit()

        repo = CyclesRepo(session)
        cycle = await repo.get_active()
        assert cycle is not None
        assert cycle.id == newer_id
        assert cycle.cycle_date == newer_date
    finally:
        await _cleanup_season(session, season_id)


# ---------------------------------------------------------------------------
# Tests — update_state
# ---------------------------------------------------------------------------


async def test_update_state_changes_state(session: AsyncSession) -> None:
    season_id = await _make_season(session, "upd-001", active=False)
    chapter_id = await _make_chapter(session, season_id)
    await session.commit()
    try:
        cycle_id = await _make_cycle(session, season_id, chapter_id)
        await session.commit()

        repo = CyclesRepo(session)
        await repo.update_state(cycle_id, "ESTRENO")
        await session.commit()

        row = await session.execute(
            sa.text("SELECT state FROM cycles WHERE id = :id"), {"id": cycle_id}
        )
        assert row.scalar_one() == "ESTRENO"
    finally:
        await _cleanup_season(session, season_id)


async def test_update_state_sets_next_chapter_id(session: AsyncSession) -> None:
    season_id = await _make_season(session, "upd-nci-001", active=False)
    chapter_id = await _make_chapter(session, season_id)
    await session.commit()
    try:
        cycle_id = await _make_cycle(session, season_id, chapter_id)
        await session.commit()

        # Insert a second chapter to serve as next_chapter_id.
        next_ch = await session.execute(
            sa.text(
                "INSERT INTO chapters "
                "(season_id, day_index, title, synopsis, manifest_json, status) "
                "VALUES (:sid, 2, 'Next', 'S', '{}', 'draft') RETURNING id"
            ),
            {"sid": season_id},
        )
        next_chapter_id = int(next_ch.scalar_one())
        await session.commit()

        repo = CyclesRepo(session)
        await repo.update_state(
            cycle_id, "PENDING_RELEASE", next_chapter_id=next_chapter_id
        )
        await session.commit()

        row = await session.execute(
            sa.text(
                "SELECT state, next_chapter_id FROM cycles WHERE id = :id"
            ),
            {"id": cycle_id},
        )
        r = row.mappings().one()
        assert r["state"] == "PENDING_RELEASE"
        assert int(r["next_chapter_id"]) == next_chapter_id
    finally:
        await _cleanup_season(session, season_id)


async def test_update_state_preserves_next_chapter_id_when_none(
    session: AsyncSession,
) -> None:
    """Passing next_chapter_id=None must not overwrite an existing value."""
    season_id = await _make_season(session, "upd-preserve-001", active=False)
    chapter_id = await _make_chapter(session, season_id)
    await session.commit()
    try:
        cycle_id = await _make_cycle(session, season_id, chapter_id)
        # Set a next_chapter_id first.
        next_ch = await session.execute(
            sa.text(
                "INSERT INTO chapters "
                "(season_id, day_index, title, synopsis, manifest_json, status) "
                "VALUES (:sid, 2, 'N', 'S', '{}', 'draft') RETURNING id"
            ),
            {"sid": season_id},
        )
        next_chapter_id = int(next_ch.scalar_one())
        await session.execute(
            sa.text("UPDATE cycles SET next_chapter_id = :nci WHERE id = :id"),
            {"nci": next_chapter_id, "id": cycle_id},
        )
        await session.commit()

        repo = CyclesRepo(session)
        await repo.update_state(cycle_id, "GENERACION", next_chapter_id=None)
        await session.commit()

        row = await session.execute(
            sa.text("SELECT next_chapter_id FROM cycles WHERE id = :id"),
            {"id": cycle_id},
        )
        stored = row.scalar_one()
        assert stored is not None
        assert int(stored) == next_chapter_id
    finally:
        await _cleanup_season(session, season_id)


# ---------------------------------------------------------------------------
# Tests — lock_advisory
# ---------------------------------------------------------------------------


async def test_lock_advisory_acquires_without_contention(
    session: AsyncSession,
) -> None:
    """Advisory lock succeeds when no other session holds the same lock."""
    season_id = await _make_season(session, "lock-001", active=False)
    chapter_id = await _make_chapter(session, season_id)
    await session.commit()
    try:
        cycle_id = await _make_cycle(session, season_id, chapter_id)
        await session.commit()

        repo = CyclesRepo(session)
        # Should not raise — no contention.
        await repo.lock_advisory(cycle_id)
        # Lock released on commit.
        await session.commit()
    finally:
        await _cleanup_season(session, season_id)


async def test_lock_busy_exception_is_importable() -> None:
    """LockBusy is a proper Exception subclass with cycle_id attribute."""
    err = LockBusy(42)
    assert isinstance(err, Exception)
    assert err.cycle_id == 42
    assert "42" in str(err)
