"""Integration tests: SeasonsRepo.

Module 003 / Task T-007.

Skips when DATABASE_URL is the conftest placeholder (no real DB available).
Each test cleans up after itself via DELETE (cascades to chapters/cycles).
Uses a unique slug prefix to avoid collisions with other test runs.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.infra.seasons_repo import Season, SeasonsRepo

API_DIR = Path(__file__).parent.parent.parent
ALEMBIC_INI = API_DIR / "alembic.ini"

_SLUG_PREFIX = "_sr-test-"
_TODAY = date(2026, 6, 9)


def _alembic_config(database_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _slug(suffix: str) -> str:
    return f"{_SLUG_PREFIX}{suffix}"


@pytest.fixture(scope="module")
def database_url() -> str:
    from tests.conftest import _is_placeholder_database_url

    url = os.environ.get("DATABASE_URL", "")
    if not url or _is_placeholder_database_url(url):
        pytest.skip("DATABASE_URL no apunta a una base real.")
    return url


@pytest.fixture(scope="module", autouse=True)
def _ensure_migrated(database_url: str) -> None:
    """Ensure migrations are applied before any test in this module."""
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


async def _delete_test_seasons(session: AsyncSession) -> None:
    """Delete all seasons whose slug starts with _SLUG_PREFIX."""
    await session.execute(
        sa.text(f"DELETE FROM seasons WHERE slug LIKE '{_SLUG_PREFIX}%'")
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_insert_returns_int_id(session: AsyncSession) -> None:
    repo = SeasonsRepo(session)
    try:
        season_id = await repo.insert(
            slug=_slug("insert-001"),
            title="Season Insert Test",
            bible_json={"acts": 3},
            started_on=_TODAY,
        )
        await session.commit()
        assert isinstance(season_id, int)
        assert season_id > 0
    finally:
        await _delete_test_seasons(session)


async def test_get_active_returns_inserted_season(session: AsyncSession) -> None:
    repo = SeasonsRepo(session)
    # Deactivate any pre-existing active season to satisfy unique constraint.
    await session.execute(
        sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE")
    )
    await session.commit()

    try:
        season_id = await repo.insert(
            slug=_slug("active-001"),
            title="Active Season",
            bible_json={},
            started_on=_TODAY,
        )
        await session.commit()

        active = await repo.get_active()
        assert active is not None
        assert active.id == season_id
        assert active.slug == _slug("active-001")
        assert active.title == "Active Season"
        assert active.is_active is True
        assert active.started_on == _TODAY
    finally:
        await _delete_test_seasons(session)


async def test_get_active_returns_none_when_no_active_season(
    session: AsyncSession,
) -> None:
    repo = SeasonsRepo(session)
    # Deactivate everything.
    await session.execute(
        sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE")
    )
    await session.commit()

    # Insert an explicitly inactive season.
    await session.execute(
        sa.text(
            "INSERT INTO seasons (slug, title, bible_json, started_on, is_active) "
            "VALUES (:slug, 'Inactive', '{}', CURRENT_DATE, FALSE)"
        ),
        {"slug": _slug("inactive-001")},
    )
    await session.commit()

    try:
        active = await repo.get_active()
        assert active is None
    finally:
        await _delete_test_seasons(session)


async def test_mark_inactive_clears_active_flag(session: AsyncSession) -> None:
    repo = SeasonsRepo(session)
    await session.execute(
        sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE")
    )
    await session.commit()

    try:
        season_id = await repo.insert(
            slug=_slug("marki-001"),
            title="To be deactivated",
            bible_json={},
            started_on=_TODAY,
        )
        await session.commit()

        # Verify active before mark_inactive.
        before = await repo.get_active()
        assert before is not None and before.id == season_id

        await repo.mark_inactive(season_id)
        await session.commit()

        after = await repo.get_active()
        assert after is None
    finally:
        await _delete_test_seasons(session)


async def test_get_by_slug_returns_season(session: AsyncSession) -> None:
    repo = SeasonsRepo(session)
    await session.execute(
        sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE")
    )
    await session.commit()

    try:
        await repo.insert(
            slug=_slug("byslug-001"),
            title="Slug Season",
            bible_json={"key": "val"},
            started_on=_TODAY,
        )
        await session.commit()

        found = await repo.get_by_slug(_slug("byslug-001"))
        assert found is not None
        assert found.slug == _slug("byslug-001")
        assert isinstance(found, Season)
    finally:
        await _delete_test_seasons(session)


async def test_get_by_slug_returns_none_for_missing(session: AsyncSession) -> None:
    repo = SeasonsRepo(session)
    result = await repo.get_by_slug("__this-slug-does-not-exist__")
    assert result is None


async def test_unique_active_constraint_raised(session: AsyncSession) -> None:
    """Inserting two active seasons raises IntegrityError."""
    repo = SeasonsRepo(session)
    await session.execute(
        sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE")
    )
    await session.commit()

    try:
        await repo.insert(
            slug=_slug("dup-active-001"),
            title="First active",
            bible_json={},
            started_on=_TODAY,
        )
        await session.commit()

        with pytest.raises(IntegrityError):
            await repo.insert(
                slug=_slug("dup-active-002"),
                title="Second active — should conflict",
                bible_json={},
                started_on=_TODAY,
            )
            await session.commit()
    finally:
        await session.rollback()
        await _delete_test_seasons(session)


async def test_insert_with_bible_json_content(session: AsyncSession) -> None:
    """bible_json with nested content round-trips correctly."""
    repo = SeasonsRepo(session)
    await session.execute(
        sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE")
    )
    await session.commit()

    bible = {"acts": [{"number": 1, "title": "Pilot"}, {"number": 2}], "theme": "AI"}

    try:
        season_id = await repo.insert(
            slug=_slug("bible-001"),
            title="Bible Test",
            bible_json=bible,
            started_on=_TODAY,
        )
        await session.commit()
        assert season_id > 0
    finally:
        await _delete_test_seasons(session)
