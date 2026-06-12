"""Integration tests: TransitionsRepo.

Module 003 / Task T-010.

Skips when DATABASE_URL is the conftest placeholder (no real DB available).
Each test creates its own season + chapter + cycle and deletes them in
finally blocks in FK-safe order (cycles → seasons).

Key assertion: ON CONFLICT DO NOTHING idempotency — inserting the same
(cycle_id, to_state, trigger_id) twice returns None the second time.
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
from app.infra.transitions_repo import TransitionRow, TransitionsRepo

API_DIR = Path(__file__).parent.parent.parent
ALEMBIC_INI = API_DIR / "alembic.ini"

_SLUG_PREFIX = "_tr-test-"
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


async def _make_cycle(session: AsyncSession, suffix: str) -> int:
    """Create a minimal inactive season + chapter + cycle; return cycle_id."""
    result = await session.execute(
        sa.text(
            "INSERT INTO seasons (slug, title, bible_json, started_on, is_active) "
            "VALUES (:slug, 'TR Test', '{}', :today, FALSE) RETURNING id"
        ),
        {"slug": f"{_SLUG_PREFIX}{suffix}", "today": _TODAY},
    )
    season_id = int(result.scalar_one())

    result2 = await session.execute(
        sa.text(
            "INSERT INTO chapters "
            "(season_id, day_index, title, synopsis, manifest_json, status) "
            "VALUES (:sid, 1, 'T', 'S', '{}', 'ready') RETURNING id"
        ),
        {"sid": season_id},
    )
    chapter_id = int(result2.scalar_one())

    result3 = await session.execute(
        sa.text(
            "INSERT INTO cycles (season_id, chapter_id, state, cycle_date) "
            "VALUES (:sid, :cid, 'PENDING_RELEASE', :today) RETURNING id"
        ),
        {"sid": season_id, "cid": chapter_id, "today": _TODAY},
    )
    await session.commit()
    return int(result3.scalar_one())


async def _cleanup_by_cycle(session: AsyncSession, cycle_id: int) -> None:
    """Delete the cycle's season + descendants in FK-safe order.

    cycles.season_id has no ON DELETE CASCADE, so seasons cannot be deleted
    while any cycle still references them.  Order: delete cycles first
    (cascades state_transitions), then seasons (cascades chapters).
    """
    row = await session.execute(
        sa.text("SELECT season_id FROM cycles WHERE id = :cid"),
        {"cid": cycle_id},
    )
    season_id = row.scalar_one()
    await session.execute(
        sa.text("DELETE FROM cycles WHERE season_id = :sid"),
        {"sid": season_id},
    )
    await session.execute(
        sa.text("DELETE FROM seasons WHERE id = :sid"),
        {"sid": season_id},
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Tests — insert
# ---------------------------------------------------------------------------


async def test_insert_returns_transition_row(session: AsyncSession) -> None:
    cycle_id = await _make_cycle(session, "ins-001")
    repo = TransitionsRepo(session)
    try:
        row = await repo.insert(
            cycle_id=cycle_id,
            from_state="PENDING_RELEASE",
            to_state="ESTRENO",
            triggered_by="cron",
            trigger_id="gh-run-001",
        )
        await session.commit()
        assert row is not None
        assert isinstance(row, TransitionRow)
        assert row.cycle_id == cycle_id
        assert row.from_state == "PENDING_RELEASE"
        assert row.to_state == "ESTRENO"
        assert row.triggered_by == "cron"
        assert row.trigger_id == "gh-run-001"
        assert row.payload_json is None
        assert row.id > 0
    finally:
        await _cleanup_by_cycle(session, cycle_id)


async def test_insert_idempotency_returns_none_on_duplicate(
    session: AsyncSession,
) -> None:
    """Second insert with same (cycle_id, to_state, trigger_id) returns None."""
    cycle_id = await _make_cycle(session, "idem-001")
    repo = TransitionsRepo(session)
    try:
        first = await repo.insert(
            cycle_id=cycle_id,
            from_state="PENDING_RELEASE",
            to_state="ESTRENO",
            triggered_by="cron",
            trigger_id="gh-run-idem-001",
        )
        await session.commit()
        assert first is not None

        second = await repo.insert(
            cycle_id=cycle_id,
            from_state="PENDING_RELEASE",
            to_state="ESTRENO",
            triggered_by="cron",
            trigger_id="gh-run-idem-001",  # same trigger_id
        )
        await session.commit()
        assert second is None  # already applied
    finally:
        await _cleanup_by_cycle(session, cycle_id)


async def test_insert_null_trigger_id_always_inserts(session: AsyncSession) -> None:
    """NULL trigger_id rows bypass idempotency — each call creates a new row."""
    cycle_id = await _make_cycle(session, "null-tid-001")
    repo = TransitionsRepo(session)
    try:
        r1 = await repo.insert(
            cycle_id=cycle_id,
            from_state="FILTERING",
            to_state="VOTACION",
            triggered_by="side_effect",
            trigger_id=None,
        )
        await session.commit()
        r2 = await repo.insert(
            cycle_id=cycle_id,
            from_state="FILTERING",
            to_state="VOTACION",
            triggered_by="side_effect",
            trigger_id=None,
        )
        await session.commit()
        assert r1 is not None
        assert r2 is not None
        assert r1.id != r2.id  # two distinct rows
    finally:
        await _cleanup_by_cycle(session, cycle_id)


async def test_insert_with_payload_json(session: AsyncSession) -> None:
    cycle_id = await _make_cycle(session, "payload-001")
    repo = TransitionsRepo(session)
    payload = {"error_hash": "abc123", "run_attempt": 2}
    try:
        row = await repo.insert(
            cycle_id=cycle_id,
            from_state="GENERACION",
            to_state="FAILED",
            triggered_by="watchdog",
            trigger_id="wdg-001",
            payload_json=payload,
        )
        await session.commit()
        assert row is not None
        assert row.payload_json == payload
    finally:
        await _cleanup_by_cycle(session, cycle_id)


async def test_insert_different_trigger_ids_both_succeed(
    session: AsyncSession,
) -> None:
    """Same (cycle, to_state) but different trigger_ids: both rows inserted."""
    cycle_id = await _make_cycle(session, "diff-tid-001")
    repo = TransitionsRepo(session)
    try:
        r1 = await repo.insert(
            cycle_id=cycle_id,
            from_state="PENDING_RELEASE",
            to_state="ESTRENO",
            triggered_by="cron",
            trigger_id="gh-run-001",
        )
        await session.commit()
        r2 = await repo.insert(
            cycle_id=cycle_id,
            from_state="PENDING_RELEASE",
            to_state="ESTRENO",
            triggered_by="admin",
            trigger_id="gh-run-002",  # different trigger_id
        )
        await session.commit()
        assert r1 is not None and r2 is not None
        assert r1.id != r2.id
    finally:
        await _cleanup_by_cycle(session, cycle_id)


# ---------------------------------------------------------------------------
# Tests — get_recent
# ---------------------------------------------------------------------------


async def test_get_recent_returns_ordered_newest_first(session: AsyncSession) -> None:
    cycle_id = await _make_cycle(session, "recent-001")
    repo = TransitionsRepo(session)
    try:
        states = ["ESTRENO", "RECEPCION_IDEAS", "FILTERING"]
        for i, state in enumerate(states):
            await repo.insert(
                cycle_id=cycle_id,
                from_state="PENDING_RELEASE",
                to_state=state,
                triggered_by="admin",
                trigger_id=f"adm-{i}",
            )
        await session.commit()

        recent = await repo.get_recent(cycle_id, limit=3)
        assert len(recent) == 3
        # Most recent first.
        assert recent[0].to_state == "FILTERING"
        assert recent[1].to_state == "RECEPCION_IDEAS"
        assert recent[2].to_state == "ESTRENO"
    finally:
        await _cleanup_by_cycle(session, cycle_id)


async def test_get_recent_respects_limit(session: AsyncSession) -> None:
    cycle_id = await _make_cycle(session, "recent-lim-001")
    repo = TransitionsRepo(session)
    try:
        for i in range(5):
            await repo.insert(
                cycle_id=cycle_id,
                from_state="PENDING_RELEASE",
                to_state="ESTRENO",
                triggered_by="admin",
                trigger_id=f"lim-{i}",
            )
        await session.commit()

        recent = await repo.get_recent(cycle_id, limit=2)
        assert len(recent) == 2
    finally:
        await _cleanup_by_cycle(session, cycle_id)


async def test_get_recent_returns_empty_for_no_transitions(
    session: AsyncSession,
) -> None:
    cycle_id = await _make_cycle(session, "recent-empty-001")
    repo = TransitionsRepo(session)
    try:
        rows = await repo.get_recent(cycle_id)
        assert rows == []
    finally:
        await _cleanup_by_cycle(session, cycle_id)


# ---------------------------------------------------------------------------
# Tests — get_by_trigger
# ---------------------------------------------------------------------------


async def test_get_by_trigger_finds_row(session: AsyncSession) -> None:
    cycle_id = await _make_cycle(session, "gbt-001")
    repo = TransitionsRepo(session)
    try:
        inserted = await repo.insert(
            cycle_id=cycle_id,
            from_state="PENDING_RELEASE",
            to_state="ESTRENO",
            triggered_by="cron",
            trigger_id="gbt-trigger-001",
        )
        await session.commit()
        assert inserted is not None

        found = await repo.get_by_trigger(cycle_id, "ESTRENO", "gbt-trigger-001")
        assert found is not None
        assert isinstance(found, TransitionRow)
        assert found.id == inserted.id
        assert found.trigger_id == "gbt-trigger-001"
    finally:
        await _cleanup_by_cycle(session, cycle_id)


async def test_get_by_trigger_returns_none_for_missing(
    session: AsyncSession,
) -> None:
    cycle_id = await _make_cycle(session, "gbt-miss-001")
    repo = TransitionsRepo(session)
    try:
        result = await repo.get_by_trigger(
            cycle_id, "ESTRENO", "__nonexistent-trigger__"
        )
        assert result is None
    finally:
        await _cleanup_by_cycle(session, cycle_id)
