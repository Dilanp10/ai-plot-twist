"""Integration tests: cycle_executor.transition — happy paths.

Module 003 / Task T-012.

Skips when DATABASE_URL is the conftest placeholder (no real DB available).

Covers:
  - PENDING_RELEASE → ESTRENO: applied, chapter marked live, cycle updated,
    state_transitions row created, side_effect_name=None.
  - already_applied: same trigger_id returns 200 with original applied_at.
  - KillSwitchActive: kill switch on → exception raised.
  - NoActiveCycle: no active season → exception raised.
  - TimeFenceViolation: skip_dwell=False on fresh cycle → exception raised.
  - IllegalTransition: invalid state pair → exception raised.
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
from app.domain.cycle_executor import (
    KillSwitchActive,
    NoActiveCycle,
    TransitionResult,
    transition,
)
from app.domain.cycle_fsm import IllegalTransition, TimeFenceViolation
from app.infra.chapters_repo import ChaptersRepo
from app.infra.system_flags_repo import SystemFlagsRepo, clear_cache
from app.infra.transitions_repo import TransitionsRepo

API_DIR = Path(__file__).parent.parent.parent
ALEMBIC_INI = API_DIR / "alembic.ini"
_TODAY = date(2026, 6, 9)
_SLUG_PREFIX = "_ex-test-"


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


@pytest.fixture(autouse=True)
def _clear_flag_cache() -> None:
    clear_cache()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _setup_active_cycle(
    session: AsyncSession,
    suffix: str,
) -> tuple[int, int, int]:
    """Create active season + ready chapter + PENDING_RELEASE cycle.

    Returns (season_id, chapter_id, cycle_id).
    """
    # Deactivate any existing active season.
    await session.execute(
        sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE")
    )
    # Season
    r = await session.execute(
        sa.text(
            "INSERT INTO seasons (slug, title, bible_json, started_on, is_active) "
            "VALUES (:slug, 'Exec Test', '{}', :today, TRUE) RETURNING id"
        ),
        {"slug": f"{_SLUG_PREFIX}{suffix}", "today": _TODAY},
    )
    season_id = int(r.scalar_one())
    # Chapter
    r2 = await session.execute(
        sa.text(
            "INSERT INTO chapters "
            "(season_id, day_index, title, synopsis, manifest_json, status) "
            "VALUES (:sid, 1, 'T', 'S', '{}', 'ready') RETURNING id"
        ),
        {"sid": season_id},
    )
    chapter_id = int(r2.scalar_one())
    # Cycle
    r3 = await session.execute(
        sa.text(
            "INSERT INTO cycles (season_id, chapter_id, state, cycle_date) "
            "VALUES (:sid, :cid, 'PENDING_RELEASE', :today) RETURNING id"
        ),
        {"sid": season_id, "cid": chapter_id, "today": _TODAY},
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
# Tests — happy path
# ---------------------------------------------------------------------------


async def test_pending_release_to_estreno_applied(session: AsyncSession) -> None:
    """Full happy path: PENDING_RELEASE → ESTRENO.

    Verifies:
    - result.status == "applied"
    - result.side_effect_name == "push_fanout"  (module 011 T-010)
    - cycle.state == "ESTRENO" in DB
    - chapter.status == "live" and released_at is not None
    - state_transitions row exists with correct fields
    """
    season_id, chapter_id, cycle_id = await _setup_active_cycle(session, "hp-001")

    try:
        result = await transition(
            session,
            requested_to="ESTRENO",
            triggered_by="cron",
            trigger_id="gh-run-happy-001",
            skip_dwell=True,
        )

        assert isinstance(result, TransitionResult)
        assert result.status == "applied"
        assert result.cycle_id == cycle_id
        assert result.chapter_id == chapter_id
        assert result.transition_id is not None
        assert result.side_effect_name == "push_fanout"
        assert result.applied_at is not None

        # Cycle state updated in DB.
        row = await session.execute(
            sa.text("SELECT state FROM cycles WHERE id = :id"), {"id": cycle_id}
        )
        assert row.scalar_one() == "ESTRENO"

        # Chapter marked live.
        ch = await ChaptersRepo(session).get_by_id(chapter_id)
        assert ch is not None
        assert ch.status == "live"
        assert ch.released_at is not None

        # state_transitions row persisted.
        tr_rows = await TransitionsRepo(session).get_recent(cycle_id, limit=1)
        assert len(tr_rows) == 1
        assert tr_rows[0].from_state == "PENDING_RELEASE"
        assert tr_rows[0].to_state == "ESTRENO"
        assert tr_rows[0].triggered_by == "cron"
        assert tr_rows[0].trigger_id == "gh-run-happy-001"

    finally:
        await _cleanup(session, season_id)


async def test_recepcion_ideas_to_filtering_spawns_side_effect(
    session: AsyncSession,
) -> None:
    """RECEPCION_IDEAS → FILTERING: result.side_effect_name == 'director_filter'."""
    season_id, _chapter_id, cycle_id = await _setup_active_cycle(session, "sfx-001")

    try:
        # Advance to RECEPCION_IDEAS first.
        await session.execute(
            sa.text(
                "UPDATE cycles SET state = 'RECEPCION_IDEAS', "
                "state_entered_at = now() - interval '6 hours' WHERE id = :id"
            ),
            {"id": cycle_id},
        )
        await session.commit()

        result = await transition(
            session,
            requested_to="FILTERING",
            triggered_by="cron",
            trigger_id="gh-run-sfx-001",
            skip_dwell=True,
        )

        assert result.status == "applied"
        assert result.side_effect_name == "director_filter"

    finally:
        await _cleanup(session, season_id)


# ---------------------------------------------------------------------------
# Tests — idempotency
# ---------------------------------------------------------------------------


async def test_same_trigger_id_returns_already_applied(
    session: AsyncSession,
) -> None:
    """Second call with the same trigger_id returns already_applied."""
    season_id, _chapter_id, _cycle_id = await _setup_active_cycle(session, "idem-001")

    try:
        # First call.
        first = await transition(
            session,
            requested_to="ESTRENO",
            triggered_by="cron",
            trigger_id="gh-run-idem-001",
            skip_dwell=True,
        )
        assert first.status == "applied"

        # Second call — cycle is now in ESTRENO but idempotency check should
        # fire before FSM validation.
        second = await transition(
            session,
            requested_to="ESTRENO",
            triggered_by="cron",
            trigger_id="gh-run-idem-001",  # same trigger_id
            skip_dwell=True,
        )
        assert second.status == "already_applied"
        assert second.transition_id is None
        assert second.applied_at == first.applied_at

    finally:
        await _cleanup(session, season_id)


# ---------------------------------------------------------------------------
# Tests — kill switch
# ---------------------------------------------------------------------------


async def test_kill_switch_active_raises(session: AsyncSession) -> None:
    """When kill switch is on, KillSwitchActive is raised before any DB mutation."""
    season_id, _chapter_id, cycle_id = await _setup_active_cycle(session, "ks-001")
    flags = SystemFlagsRepo(session)

    try:
        await flags.set("kill_switch", {"on": True, "reason": "test"}, updated_by="t")
        await session.commit()
        clear_cache()

        with pytest.raises(KillSwitchActive) as exc_info:
            await transition(
                session,
                requested_to="ESTRENO",
                triggered_by="cron",
                trigger_id="gh-run-ks-001",
                skip_dwell=True,
            )
        await session.rollback()

        err = exc_info.value
        assert err.reason == "test"

        # Verify NO state_transitions row was created.
        tr_rows = await TransitionsRepo(session).get_recent(cycle_id, limit=5)
        assert tr_rows == []

    finally:
        clear_cache()
        await flags.set(
            "kill_switch", {"on": False, "reason": None}, updated_by="migration"
        )
        await session.commit()
        await _cleanup(session, season_id)


# ---------------------------------------------------------------------------
# Tests — no active cycle
# ---------------------------------------------------------------------------


async def test_no_active_cycle_raises(session: AsyncSession) -> None:
    """NoActiveCycle is raised when all seasons are inactive."""
    # Deactivate everything.
    await session.execute(
        sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE")
    )
    await session.commit()

    with pytest.raises(NoActiveCycle):
        await transition(
            session,
            requested_to="ESTRENO",
            triggered_by="cron",
            trigger_id="gh-run-nocy-001",
            skip_dwell=True,
        )
    await session.rollback()


# ---------------------------------------------------------------------------
# Tests — FSM validation
# ---------------------------------------------------------------------------


async def test_illegal_transition_raises(session: AsyncSession) -> None:
    """An invalid state pair raises IllegalTransition (409)."""
    season_id, _chapter_id, _cycle_id = await _setup_active_cycle(session, "ill-001")

    try:
        with pytest.raises(IllegalTransition) as exc_info:
            await transition(
                session,
                requested_to="GENERACION",   # PENDING_RELEASE → GENERACION is illegal
                triggered_by="admin",
                trigger_id="gh-run-ill-001",
                skip_dwell=True,
            )
        await session.rollback()

        err = exc_info.value
        assert err.from_state == "PENDING_RELEASE"
        assert err.to_state == "GENERACION"

    finally:
        await _cleanup(session, season_id)


async def test_time_fence_violation_raises(session: AsyncSession) -> None:
    """Fresh cycle (0 s dwell) raises TimeFenceViolation for ESTRENO (min 60 s)."""
    season_id, _chapter_id, _cycle_id = await _setup_active_cycle(session, "tfv-001")

    try:
        # First advance to ESTRENO without the fence check.
        first = await transition(
            session,
            requested_to="ESTRENO",
            triggered_by="cron",
            trigger_id="gh-tfv-setup",
            skip_dwell=True,
        )
        assert first.status == "applied"

        # Now try to advance from freshly-entered ESTRENO → RECEPCION_IDEAS
        # WITHOUT skip_dwell (min dwell = 60 s, elapsed ≈ 0 s).
        with pytest.raises(TimeFenceViolation) as exc_info:
            await transition(
                session,
                requested_to="RECEPCION_IDEAS",
                triggered_by="cron",
                trigger_id="gh-tfv-fence",
                skip_dwell=False,    # enforce the fence
            )
        await session.rollback()

        err = exc_info.value
        assert err.from_state == "ESTRENO"
        assert err.to_state == "RECEPCION_IDEAS"
        assert err.min_dwell_s == 60

    finally:
        await _cleanup(session, season_id)
