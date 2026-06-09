"""Integration tests: ChaptersRepo.

Module 003 / Task T-008.

Skips when DATABASE_URL is the conftest placeholder (no real DB available).
Each test creates a fresh season + chapters and deletes them in finally blocks.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.infra.chapters_repo import Chapter, ChaptersRepo

API_DIR = Path(__file__).parent.parent.parent
ALEMBIC_INI = API_DIR / "alembic.ini"

_SLUG_PREFIX = "_cr-test-"
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

_MANIFEST: dict[str, object] = {"panels": [{"id": 1, "text": "stub"}]}


async def _make_season(session: AsyncSession, suffix: str) -> int:
    """Insert a minimal inactive test season and return its id."""
    result = await session.execute(
        sa.text(
            "INSERT INTO seasons (slug, title, bible_json, started_on, is_active) "
            "VALUES (:slug, 'CR Test Season', '{}', :today, FALSE) "
            "RETURNING id"
        ),
        {"slug": f"{_SLUG_PREFIX}{suffix}", "today": _TODAY},
    )
    return int(result.scalar_one())


async def _cleanup_season(session: AsyncSession, season_id: int) -> None:
    """Delete test season (cascades chapters/cycles)."""
    await session.execute(
        sa.text("DELETE FROM seasons WHERE id = :id"), {"id": season_id}
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_insert_returns_int_id(session: AsyncSession) -> None:
    season_id = await _make_season(session, "ins-001")
    await session.commit()
    repo = ChaptersRepo(session)
    try:
        chapter_id = await repo.insert(
            season_id=season_id,
            day_index=1,
            title="Chapter One",
            synopsis="The beginning",
            manifest_json=_MANIFEST,
        )
        await session.commit()
        assert isinstance(chapter_id, int)
        assert chapter_id > 0
    finally:
        await _cleanup_season(session, season_id)


async def test_get_by_id_returns_chapter(session: AsyncSession) -> None:
    season_id = await _make_season(session, "gbi-001")
    await session.commit()
    repo = ChaptersRepo(session)
    try:
        chapter_id = await repo.insert(
            season_id=season_id,
            day_index=1,
            title="Chapter Fetch",
            synopsis="Fetch synopsis",
            manifest_json={"panels": []},
            status="ready",
        )
        await session.commit()

        ch = await repo.get_by_id(chapter_id)
        assert ch is not None
        assert isinstance(ch, Chapter)
        assert ch.id == chapter_id
        assert ch.season_id == season_id
        assert ch.day_index == 1
        assert ch.title == "Chapter Fetch"
        assert ch.status == "ready"
        assert ch.released_at is None
        assert isinstance(ch.public_id, UUID)
    finally:
        await _cleanup_season(session, season_id)


async def test_get_by_id_returns_none_for_missing(session: AsyncSession) -> None:
    repo = ChaptersRepo(session)
    result = await repo.get_by_id(999_999_999)
    assert result is None


async def test_mark_live_sets_status_and_released_at(session: AsyncSession) -> None:
    season_id = await _make_season(session, "live-001")
    await session.commit()
    repo = ChaptersRepo(session)
    try:
        chapter_id = await repo.insert(
            season_id=season_id,
            day_index=1,
            title="To be made live",
            synopsis="S",
            manifest_json={},
            status="ready",
        )
        await session.commit()

        await repo.mark_live(chapter_id)
        await session.commit()

        ch = await repo.get_by_id(chapter_id)
        assert ch is not None
        assert ch.status == "live"
        assert ch.released_at is not None
    finally:
        await _cleanup_season(session, season_id)


async def test_list_by_season_ordered_by_day_index(session: AsyncSession) -> None:
    season_id = await _make_season(session, "list-001")
    await session.commit()
    repo = ChaptersRepo(session)
    try:
        # Insert out of order.
        for day in [3, 1, 2]:
            await repo.insert(
                season_id=season_id,
                day_index=day,
                title=f"Chapter {day}",
                synopsis=f"Day {day}",
                manifest_json={},
                status="draft",
            )
        await session.commit()

        chapters = await repo.list_by_season(season_id)
        assert len(chapters) == 3
        assert [ch.day_index for ch in chapters] == [1, 2, 3]
        assert [ch.title for ch in chapters] == [
            "Chapter 1",
            "Chapter 2",
            "Chapter 3",
        ]
    finally:
        await _cleanup_season(session, season_id)


async def test_list_by_season_returns_empty_for_no_chapters(
    session: AsyncSession,
) -> None:
    season_id = await _make_season(session, "list-empty-001")
    await session.commit()
    repo = ChaptersRepo(session)
    try:
        chapters = await repo.list_by_season(season_id)
        assert chapters == []
    finally:
        await _cleanup_season(session, season_id)


async def test_clone_manifest_creates_new_chapter(session: AsyncSession) -> None:
    season_id = await _make_season(session, "clone-001")
    await session.commit()
    repo = ChaptersRepo(session)
    try:
        src_id = await repo.insert(
            season_id=season_id,
            day_index=1,
            title="Original",
            synopsis="Original synopsis",
            manifest_json={"panels": [{"id": 1}]},
            status="live",
        )
        await session.commit()

        cloned_id = await repo.clone_manifest(src_id=src_id, next_day_index=2)
        await session.commit()

        assert cloned_id != src_id
        cloned = await repo.get_by_id(cloned_id)
        assert cloned is not None
        assert cloned.season_id == season_id
        assert cloned.day_index == 2
        assert cloned.title == "Original"        # cloned from src
        assert cloned.status == "draft"          # always starts as draft
        assert cloned.released_at is None        # no release yet

        # Original is unchanged.
        src = await repo.get_by_id(src_id)
        assert src is not None
        assert src.status == "live"
        assert src.day_index == 1
    finally:
        await _cleanup_season(session, season_id)


async def test_clone_manifest_unique_public_id(session: AsyncSession) -> None:
    """Cloned chapter gets a fresh gen_random_uuid(), not the source's."""
    season_id = await _make_season(session, "clone-uuid-001")
    await session.commit()
    repo = ChaptersRepo(session)
    try:
        src_id = await repo.insert(
            season_id=season_id,
            day_index=1,
            title="Src",
            synopsis="S",
            manifest_json={},
            status="ready",
        )
        await session.commit()

        cloned_id = await repo.clone_manifest(src_id=src_id, next_day_index=2)
        await session.commit()

        src = await repo.get_by_id(src_id)
        cloned = await repo.get_by_id(cloned_id)
        assert src is not None and cloned is not None
        assert src.public_id != cloned.public_id
    finally:
        await _cleanup_season(session, season_id)


async def test_insert_all_valid_statuses(session: AsyncSession) -> None:
    """All CHECK-valid statuses can be inserted without error."""
    season_id = await _make_season(session, "statuses-001")
    await session.commit()
    repo = ChaptersRepo(session)
    try:
        statuses = ["draft", "generating", "ready", "ready_degraded", "live", "archived"]
        for day, status in enumerate(statuses, start=1):
            ch_id = await repo.insert(
                season_id=season_id,
                day_index=day,
                title=f"Chapter status={status}",
                synopsis="S",
                manifest_json={},
                status=status,
            )
            await session.commit()
            ch = await repo.get_by_id(ch_id)
            assert ch is not None
            assert ch.status == status
    finally:
        await _cleanup_season(session, season_id)
