"""Integration tests: push_fanout dispatch on ESTRENO (Module 011 T-010).

Verifies that the cycle_executor wires ``push_fanout`` as the side effect
for the PENDING_RELEASE → ESTRENO edge (module 011 T-010), and that no
other tested transition returns ``"push_fanout"`` as its side_effect_name.

Skips when DATABASE_URL is the conftest placeholder.

Coverage:
  1. PENDING_RELEASE → ESTRENO returns side_effect_name == "push_fanout".
  2. ESTRENO → RECEPCION_IDEAS returns side_effect_name is None.
  3. RECEPCION_IDEAS → FILTERING returns side_effect_name == "director_filter"
     (not "push_fanout"), confirming the edge table is not accidentally wide.
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
from app.domain.cycle_executor import transition
from app.infra.system_flags_repo import clear_cache

API_DIR = Path(__file__).parent.parent.parent
ALEMBIC_INI = API_DIR / "alembic.ini"
_TODAY = date(2026, 6, 9)
_SLUG_PREFIX = "_push-dispatch-"


def _alembic_config(database_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


# ---------------------------------------------------------------------------
# Module-scoped fixtures (DB setup + migration)
# ---------------------------------------------------------------------------


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


@pytest.fixture(autouse=True)
def _clear_flag_cache() -> None:
    clear_cache()


# ---------------------------------------------------------------------------
# Seed helper
# ---------------------------------------------------------------------------


async def _make_cycle(
    session: AsyncSession,
    suffix: str,
    state: str,
) -> tuple[int, int, int]:
    """Create active season + chapter + cycle in *state*. Return (season, chapter, cycle) ids."""
    await session.execute(
        sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE")
    )
    r = await session.execute(
        sa.text(
            "INSERT INTO seasons (slug, title, bible_json, started_on, is_active) "
            "VALUES (:slug, 'PushDispatch', '{}', :today, TRUE) RETURNING id"
        ),
        {"slug": f"{_SLUG_PREFIX}{suffix}", "today": _TODAY},
    )
    season_id = int(r.scalar_one())
    r2 = await session.execute(
        sa.text(
            "INSERT INTO chapters "
            "(season_id, day_index, title, synopsis, manifest_json, status) "
            "VALUES (:sid, 1, 'T', 'S', '{}', 'ready') RETURNING id"
        ),
        {"sid": season_id},
    )
    chapter_id = int(r2.scalar_one())
    r3 = await session.execute(
        sa.text(
            "INSERT INTO cycles (season_id, chapter_id, state, cycle_date) "
            "VALUES (:sid, :cid, :state, :today) RETURNING id"
        ),
        {"sid": season_id, "cid": chapter_id, "state": state, "today": _TODAY},
    )
    cycle_id = int(r3.scalar_one())
    await session.commit()
    return season_id, chapter_id, cycle_id


async def _cleanup(session: AsyncSession, season_id: int) -> None:
    await session.execute(
        sa.text("DELETE FROM seasons WHERE id = :id"), {"id": season_id}
    )
    await session.commit()


# ---------------------------------------------------------------------------
# 1. PENDING_RELEASE → ESTRENO fires push_fanout
# ---------------------------------------------------------------------------


async def test_estreno_transition_dispatches_push_fanout(
    session: AsyncSession,
) -> None:
    season_id, _, _ = await _make_cycle(session, "pd-001", "PENDING_RELEASE")
    try:
        result = await transition(
            session,
            requested_to="ESTRENO",
            triggered_by="cron",
            trigger_id="push-dispatch-001",
            skip_dwell=True,
        )
        assert result.status == "applied"
        assert result.side_effect_name == "push_fanout"
    finally:
        await _cleanup(session, season_id)


# ---------------------------------------------------------------------------
# 2. ESTRENO → RECEPCION_IDEAS has no side effect
# ---------------------------------------------------------------------------


async def test_recepcion_ideas_transition_has_no_side_effect(
    session: AsyncSession,
) -> None:
    season_id, _, _ = await _make_cycle(session, "pd-002", "ESTRENO")
    try:
        result = await transition(
            session,
            requested_to="RECEPCION_IDEAS",
            triggered_by="cron",
            trigger_id="push-dispatch-002",
            skip_dwell=True,
        )
        assert result.status == "applied"
        assert result.side_effect_name is None
    finally:
        await _cleanup(session, season_id)


# ---------------------------------------------------------------------------
# 3. RECEPCION_IDEAS → FILTERING dispatches director_filter (not push_fanout)
# ---------------------------------------------------------------------------


async def test_filtering_transition_dispatches_director_filter_not_push(
    session: AsyncSession,
) -> None:
    season_id, _, _ = await _make_cycle(session, "pd-003", "RECEPCION_IDEAS")
    try:
        result = await transition(
            session,
            requested_to="FILTERING",
            triggered_by="cron",
            trigger_id="push-dispatch-003",
            skip_dwell=True,
        )
        assert result.status == "applied"
        assert result.side_effect_name == "director_filter"
        assert result.side_effect_name != "push_fanout"
    finally:
        await _cleanup(session, season_id)
