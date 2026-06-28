"""Integration tests: watchdog.check — stuck-state detection.

Module 003 / Task T-014.

Skips when DATABASE_URL is the conftest placeholder (no real DB available).

Covers every verdict from the R-004 table:
  ready_for_release  — PENDING_RELEASE
  ok_in_progress     — GENERACION, elapsed 30 min (< grace 60 min)
  already_failed     — FAILED state
  no_active_cycle    — no active season
  stuck_generation   — GENERACION, elapsed 90 min (> grace 60 min) → FAILED
  stuck_voting       — VOTACION → FAILED
  stuck_filtering    — FILTERING → FAILED
  stuck_reception    — RECEPCION_IDEAS → FAILED
  impossible_state   — ESTRENO → FAILED

Also verifies Discord integration via _post_discord mock.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.domain import watchdog as wd
from app.domain.watchdog import GENERATION_GRACE_S, WatchdogResult, check
from app.infra.transitions_repo import TransitionsRepo

API_DIR = Path(__file__).parent.parent.parent
ALEMBIC_INI = API_DIR / "alembic.ini"
_TODAY = date(2026, 6, 9)
# 23:55 ART = 02:55 UTC next day
_NOW_UTC = datetime(2026, 6, 10, 2, 55, 0, tzinfo=UTC)
_SLUG_PREFIX = "_wd-test-"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
# DB helpers
# ---------------------------------------------------------------------------


async def _setup_cycle(
    session: AsyncSession,
    state: str,
    suffix: str,
    entered_before_s: float = 0.0,
) -> tuple[int, int, int]:
    """Create active season + chapter + cycle in *state*.

    ``entered_before_s`` controls how many seconds before *_NOW_UTC* the cycle
    entered the state, so tests can simulate elapsed time.

    Returns (season_id, chapter_id, cycle_id).
    """
    await session.execute(
        sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE")
    )
    r = await session.execute(
        sa.text(
            "INSERT INTO seasons (slug, title, bible_json, started_on, is_active) "
            "VALUES (:slug, 'WD Test', '{}', :today, TRUE) RETURNING id"
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
    entered_at = _NOW_UTC - timedelta(seconds=entered_before_s)
    r3 = await session.execute(
        sa.text(
            "INSERT INTO cycles "
            "(season_id, chapter_id, state, cycle_date, state_entered_at) "
            "VALUES (:sid, :cid, :state, :today, :entered_at) RETURNING id"
        ),
        {
            "sid": season_id,
            "cid": chapter_id,
            "state": state,
            "today": _TODAY,
            "entered_at": entered_at,
        },
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
# Tests — healthy verdicts (no DB mutation)
# ---------------------------------------------------------------------------


async def test_pending_release_returns_ready_for_release(
    session: AsyncSession,
) -> None:
    """PENDING_RELEASE → ready_for_release, no FAILED transition."""
    season_id, _, cycle_id = await _setup_cycle(
        session, "PENDING_RELEASE", "pr-001", entered_before_s=600
    )

    try:
        result = await check(session, _NOW_UTC)

        assert isinstance(result, WatchdogResult)
        assert result.verdict == "ready_for_release"
        assert result.cycle_id == cycle_id
        assert result.cycle_state == "PENDING_RELEASE"
        assert result.forced_failed is False
        assert result.discord_posted is False

        # Cycle state must not have changed.
        row = await session.execute(
            sa.text("SELECT state FROM cycles WHERE id = :id"), {"id": cycle_id}
        )
        assert row.scalar_one() == "PENDING_RELEASE"

        # No FAILED transition row.
        tr_rows = await TransitionsRepo(session).get_recent(cycle_id, limit=5)
        assert all(r.to_state != "FAILED" for r in tr_rows)

    finally:
        await _cleanup(session, season_id)


async def test_generacion_under_grace_returns_ok_in_progress(
    session: AsyncSession,
) -> None:
    """GENERACION with elapsed 30 min < 60 min grace → ok_in_progress."""
    season_id, _, _cycle_id = await _setup_cycle(
        session, "GENERACION", "gen-ok-001", entered_before_s=30 * 60
    )

    try:
        result = await check(session, _NOW_UTC)

        assert result.verdict == "ok_in_progress"
        assert result.forced_failed is False
        assert result.elapsed_seconds is not None
        assert result.elapsed_seconds < GENERATION_GRACE_S

    finally:
        await _cleanup(session, season_id)


async def test_already_failed_returns_already_failed(
    session: AsyncSession,
) -> None:
    """FAILED → already_failed, no action."""
    season_id, _, cycle_id = await _setup_cycle(
        session, "FAILED", "fail-001", entered_before_s=100
    )

    try:
        result = await check(session, _NOW_UTC)

        assert result.verdict == "already_failed"
        assert result.forced_failed is False

        row = await session.execute(
            sa.text("SELECT state FROM cycles WHERE id = :id"), {"id": cycle_id}
        )
        assert row.scalar_one() == "FAILED"

    finally:
        await _cleanup(session, season_id)


async def test_no_active_cycle_returns_no_active_cycle(
    session: AsyncSession,
) -> None:
    """No active season → no_active_cycle verdict, no error."""
    await session.execute(
        sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE")
    )
    await session.commit()

    result = await check(session, _NOW_UTC)

    assert result.verdict == "no_active_cycle"
    assert result.cycle_id is None
    assert result.forced_failed is False


# ---------------------------------------------------------------------------
# Tests — stuck verdicts (force FAILED)
# ---------------------------------------------------------------------------


async def test_generacion_over_grace_stays_healthy(
    session: AsyncSession,
) -> None:
    """GENERACION never forced to FAILED — manual upload has no time limit (módulo 014).

    Even at 90 min (well past the legacy 60-min grace) the watchdog treats
    GENERACION as ``ok_in_progress`` and leaves the cycle untouched, because
    the phase now waits for the operator's manual video upload.
    """
    season_id, _, cycle_id = await _setup_cycle(
        session, "GENERACION", "gen-stuck-001", entered_before_s=90 * 60
    )

    try:
        result = await check(session, _NOW_UTC)

        assert result.verdict == "ok_in_progress"
        assert result.forced_failed is False

        # Cycle must remain in GENERACION (not FAILED) in the DB.
        row = await session.execute(
            sa.text("SELECT state FROM cycles WHERE id = :id"), {"id": cycle_id}
        )
        assert row.scalar_one() == "GENERACION"

    finally:
        await _cleanup(session, season_id)


async def test_filtering_forces_failed(session: AsyncSession) -> None:
    """FILTERING → stuck_filtering → FAILED."""
    season_id, _, cycle_id = await _setup_cycle(
        session, "FILTERING", "filt-001", entered_before_s=6 * 3600
    )

    try:
        result = await check(session, _NOW_UTC)

        assert result.verdict == "stuck_filtering"
        assert result.forced_failed is True

        row = await session.execute(
            sa.text("SELECT state FROM cycles WHERE id = :id"), {"id": cycle_id}
        )
        assert row.scalar_one() == "FAILED"

    finally:
        await _cleanup(session, season_id)


async def test_votacion_forces_failed(session: AsyncSession) -> None:
    """VOTACION → stuck_voting → FAILED."""
    season_id, _, cycle_id = await _setup_cycle(
        session, "VOTACION", "vot-001", entered_before_s=5 * 3600
    )

    try:
        result = await check(session, _NOW_UTC)

        assert result.verdict == "stuck_voting"
        assert result.forced_failed is True

        row = await session.execute(
            sa.text("SELECT state FROM cycles WHERE id = :id"), {"id": cycle_id}
        )
        assert row.scalar_one() == "FAILED"

    finally:
        await _cleanup(session, season_id)


async def test_recepcion_ideas_forces_failed(session: AsyncSession) -> None:
    """RECEPCION_IDEAS → stuck_reception → FAILED."""
    season_id, _, cycle_id = await _setup_cycle(
        session, "RECEPCION_IDEAS", "rec-001", entered_before_s=12 * 3600
    )

    try:
        result = await check(session, _NOW_UTC)

        assert result.verdict == "stuck_reception"
        assert result.forced_failed is True

        row = await session.execute(
            sa.text("SELECT state FROM cycles WHERE id = :id"), {"id": cycle_id}
        )
        assert row.scalar_one() == "FAILED"

    finally:
        await _cleanup(session, season_id)


async def test_estreno_forces_failed_impossible_state(
    session: AsyncSession,
) -> None:
    """ESTRENO at 23:55 → impossible_state → FAILED."""
    season_id, _, cycle_id = await _setup_cycle(
        session, "ESTRENO", "imp-001", entered_before_s=12 * 3600
    )

    try:
        result = await check(session, _NOW_UTC)

        assert result.verdict == "impossible_state"
        assert result.forced_failed is True
        assert result.cycle_state == "ESTRENO"

        row = await session.execute(
            sa.text("SELECT state FROM cycles WHERE id = :id"), {"id": cycle_id}
        )
        assert row.scalar_one() == "FAILED"

    finally:
        await _cleanup(session, season_id)


# ---------------------------------------------------------------------------
# Tests — transition row payload
# ---------------------------------------------------------------------------


async def test_failed_transition_row_has_verdict_and_elapsed(
    session: AsyncSession,
) -> None:
    """The transition row payload_json contains verdict and elapsed_s."""
    season_id, _, cycle_id = await _setup_cycle(
        session, "VOTACION", "tr-001", entered_before_s=5 * 3600
    )

    try:
        result = await check(session, _NOW_UTC)
        assert result.forced_failed is True

        tr_rows = await TransitionsRepo(session).get_recent(cycle_id, limit=1)
        assert len(tr_rows) == 1
        payload = tr_rows[0].payload_json
        assert payload is not None
        assert payload["verdict"] == "stuck_voting"
        assert "elapsed_s" in payload
        assert tr_rows[0].from_state == "VOTACION"
        assert tr_rows[0].to_state == "FAILED"
        assert tr_rows[0].trigger_id is None

    finally:
        await _cleanup(session, season_id)


# ---------------------------------------------------------------------------
# Tests — Discord integration
# ---------------------------------------------------------------------------


async def test_discord_posted_on_stuck_verdict(session: AsyncSession) -> None:
    """_post_discord is invoked when a stuck verdict forces FAILED and a
    discord_webhook_url is provided."""
    season_id, _, cycle_id = await _setup_cycle(
        session, "FILTERING", "disc-001", entered_before_s=6 * 3600
    )

    try:
        with patch.object(
            wd,
            "_post_discord",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_discord:
            result = await check(
                session,
                _NOW_UTC,
                discord_webhook_url="https://discord.com/api/webhooks/test",
            )

        assert result.discord_posted is True
        mock_discord.assert_awaited_once()
        kw = mock_discord.call_args.kwargs
        assert kw["verdict"] == "stuck_filtering"
        assert kw["cycle_id"] == cycle_id
        assert kw["cycle_state"] == "FILTERING"

    finally:
        await _cleanup(session, season_id)


async def test_discord_not_posted_when_url_is_none(session: AsyncSession) -> None:
    """_post_discord is NOT called when discord_webhook_url=None."""
    season_id, _, _cycle_id = await _setup_cycle(
        session, "FILTERING", "no-disc-001", entered_before_s=6 * 3600
    )

    try:
        with patch.object(
            wd,
            "_post_discord",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_discord:
            result = await check(session, _NOW_UTC, discord_webhook_url=None)

        assert result.discord_posted is False
        mock_discord.assert_not_awaited()

    finally:
        await _cleanup(session, season_id)


async def test_discord_not_posted_on_healthy_verdict(session: AsyncSession) -> None:
    """_post_discord is not called for ready_for_release."""
    season_id, _, _cycle_id = await _setup_cycle(
        session, "PENDING_RELEASE", "no-disc-002", entered_before_s=600
    )

    try:
        with patch.object(
            wd,
            "_post_discord",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_discord:
            result = await check(
                session,
                _NOW_UTC,
                discord_webhook_url="https://discord.com/api/webhooks/test",
            )

        assert result.discord_posted is False
        mock_discord.assert_not_awaited()

    finally:
        await _cleanup(session, season_id)


# ---------------------------------------------------------------------------
# Unit tests — _compute_verdict (pure, no DB)
# ---------------------------------------------------------------------------


def test_compute_verdict_all_states() -> None:
    """_compute_verdict returns expected verdict for all states."""
    from app.domain.watchdog import _compute_verdict

    assert _compute_verdict("PENDING_RELEASE", 0) == "ready_for_release"
    # GENERACION is always healthy now (manual video upload — no time fence).
    assert _compute_verdict("GENERACION", 0) == "ok_in_progress"
    assert _compute_verdict("GENERACION", GENERATION_GRACE_S) == "ok_in_progress"
    assert _compute_verdict("GENERACION", GENERATION_GRACE_S + 10_000) == "ok_in_progress"
    assert _compute_verdict("FAILED", 999) == "already_failed"
    assert _compute_verdict("VOTACION", 0) == "stuck_voting"
    assert _compute_verdict("FILTERING", 0) == "stuck_filtering"
    assert _compute_verdict("RECEPCION_IDEAS", 0) == "stuck_reception"
    assert _compute_verdict("ESTRENO", 0) == "impossible_state"
