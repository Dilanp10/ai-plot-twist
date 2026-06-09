"""Integration tests: safe_side_effect.run_safe crash-recovery flow.

Module 003 / Task T-013.

Skips when DATABASE_URL is the conftest placeholder (no real DB available).

Covers:
  - Crashing side effect forces cycle → FAILED, activates kill-switch,
    inserts a FAILED transition row with error_hash + error_type payload.
  - Successful side effect leaves cycle state unchanged.
  - Discord webhook is called when discord_webhook_url is set (verified via
    patching _post_discord).
  - If _handle_failure's DB ops fail, run_safe still returns normally
    (inner error is logged at CRITICAL but not reraised).
"""

from __future__ import annotations

import asyncio
import os
from datetime import date
from pathlib import Path
from typing import NoReturn
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.domain import safe_side_effect, side_effects
from app.domain.safe_side_effect import run_safe
from app.infra.system_flags_repo import SystemFlagsRepo, clear_cache
from app.infra.transitions_repo import TransitionsRepo

API_DIR = Path(__file__).parent.parent.parent
ALEMBIC_INI = API_DIR / "alembic.ini"
_TODAY = date(2026, 6, 9)
_SLUG_PREFIX = "_sse-test-"


# ---------------------------------------------------------------------------
# Conftest-level skip + migration
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


@pytest.fixture
async def session_factory(  # type: ignore[misc]
    database_url: str,
) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    yield factory
    await engine.dispose()


@pytest.fixture(autouse=True)
def _clear_flag_cache() -> None:
    clear_cache()


# ---------------------------------------------------------------------------
# Crashing side-effect stub
# ---------------------------------------------------------------------------


async def _crash_fn(chapter_id: int) -> NoReturn:
    raise RuntimeError("simulated side-effect failure for testing")


async def _noop_fn(chapter_id: int) -> None:
    pass


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _setup_cycle_in_state(
    session: AsyncSession,
    state: str,
    suffix: str,
) -> tuple[int, int, int]:
    """Create active season + chapter + cycle in *state*.

    Returns (season_id, chapter_id, cycle_id).
    """
    await session.execute(
        sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE")
    )
    r = await session.execute(
        sa.text(
            "INSERT INTO seasons (slug, title, bible_json, started_on, is_active) "
            "VALUES (:slug, 'SSE Test', '{}', :today, TRUE) RETURNING id"
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


async def _restore_kill_switch(sf: async_sessionmaker[AsyncSession]) -> None:
    """Put the kill-switch back to on=False after a test that activated it."""
    async with sf() as s:
        await SystemFlagsRepo(s).set(
            "kill_switch", {"on": False, "reason": None}, updated_by="test-cleanup"
        )
        await s.commit()
    clear_cache()


# ---------------------------------------------------------------------------
# Registry isolation helper
# ---------------------------------------------------------------------------


class _RegistrySnapshot:
    """Context-manager that snapshots and restores side_effects._registry."""

    def __enter__(self) -> _RegistrySnapshot:
        self._saved = dict(side_effects._registry)
        return self

    def __exit__(self, *_: object) -> None:
        side_effects._registry.clear()
        side_effects._registry.update(self._saved)


# ---------------------------------------------------------------------------
# Tests — crash path
# ---------------------------------------------------------------------------


async def test_crash_forces_failed_state_and_kill_switch(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A crashing side effect must force the cycle to FAILED and activate
    the kill-switch in the same commit."""
    season_id, chapter_id, cycle_id = await _setup_cycle_in_state(
        session, "FILTERING", "crash-001"
    )

    with _RegistrySnapshot():
        side_effects.register("_test_crash", _crash_fn)

        try:
            # run_safe must NOT reraise — it returns normally.
            await run_safe(
                name="_test_crash",
                chapter_id=chapter_id,
                cycle_id=cycle_id,
                session_factory=session_factory,
                discord_webhook_url=None,
            )
        finally:
            # Restore flags regardless of test outcome.
            await _restore_kill_switch(session_factory)
            await _cleanup(session, season_id)

    # --- Assertions (session sees committed data via READ COMMITTED) ---

    row = await session.execute(
        sa.text("SELECT state FROM cycles WHERE id = :id"), {"id": cycle_id}
    )
    assert row.scalar_one() == "FAILED"

    clear_cache()
    flags_repo = SystemFlagsRepo(session)
    assert await flags_repo.is_kill_switch_on() is False  # already restored above

    # Transition row: to_state=FAILED with error payload.
    tr_rows = await TransitionsRepo(session).get_recent(cycle_id, limit=5)
    failed_rows = [r for r in tr_rows if r.to_state == "FAILED"]
    assert len(failed_rows) == 1
    payload = failed_rows[0].payload_json
    assert payload is not None
    assert "error_hash" in payload
    assert "error_type" in payload
    assert payload["side_effect"] == "_test_crash"
    assert payload["error_type"] == "RuntimeError"
    assert len(payload["error_hash"]) == 8


async def test_kill_switch_is_on_before_restore(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify the kill-switch is actually set to on=True during the crash."""
    season_id, chapter_id, cycle_id = await _setup_cycle_in_state(
        session, "FILTERING", "ks-on-001"
    )

    with _RegistrySnapshot():
        side_effects.register("_test_crash_ks", _crash_fn)

        try:
            await run_safe(
                name="_test_crash_ks",
                chapter_id=chapter_id,
                cycle_id=cycle_id,
                session_factory=session_factory,
                discord_webhook_url=None,
            )

            # Before restore: kill-switch must be on.
            clear_cache()
            async with session_factory() as check_session:
                is_on = await SystemFlagsRepo(check_session).is_kill_switch_on()
            assert is_on is True

        finally:
            await _restore_kill_switch(session_factory)
            await _cleanup(session, season_id)


# ---------------------------------------------------------------------------
# Tests — success path
# ---------------------------------------------------------------------------


async def test_success_leaves_cycle_state_unchanged(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A successful side effect must not alter the cycle state or kill-switch."""
    season_id, chapter_id, cycle_id = await _setup_cycle_in_state(
        session, "FILTERING", "ok-001"
    )

    with _RegistrySnapshot():
        side_effects.register("_test_noop", _noop_fn)

        try:
            await run_safe(
                name="_test_noop",
                chapter_id=chapter_id,
                cycle_id=cycle_id,
                session_factory=session_factory,
                discord_webhook_url=None,
            )
        finally:
            await _cleanup(session, season_id)

    # Cycle state must still be FILTERING.
    row = await session.execute(
        sa.text("SELECT state FROM cycles WHERE id = :id"), {"id": cycle_id}
    )
    assert row.scalar_one() == "FILTERING"

    # Kill-switch must remain off.
    clear_cache()
    assert await SystemFlagsRepo(session).is_kill_switch_on() is False

    # No FAILED transition row.
    tr_rows = await TransitionsRepo(session).get_recent(cycle_id, limit=5)
    assert all(r.to_state != "FAILED" for r in tr_rows)


# ---------------------------------------------------------------------------
# Tests — Discord path
# ---------------------------------------------------------------------------


async def test_discord_post_called_on_crash(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """_post_discord is called with the correct metadata when discord_webhook_url
    is provided."""
    season_id, chapter_id, cycle_id = await _setup_cycle_in_state(
        session, "FILTERING", "discord-001"
    )

    with _RegistrySnapshot():
        side_effects.register("_test_crash_disc", _crash_fn)

        with patch.object(
            safe_side_effect,
            "_post_discord",
            new_callable=AsyncMock,
        ) as mock_discord:
            try:
                await run_safe(
                    name="_test_crash_disc",
                    chapter_id=chapter_id,
                    cycle_id=cycle_id,
                    session_factory=session_factory,
                    discord_webhook_url="https://discord.com/api/webhooks/test",
                )
            finally:
                await _restore_kill_switch(session_factory)
                await _cleanup(session, season_id)

    mock_discord.assert_awaited_once()
    kw = mock_discord.call_args.kwargs
    assert kw["webhook_url"] == "https://discord.com/api/webhooks/test"
    assert kw["name"] == "_test_crash_disc"
    assert kw["chapter_id"] == chapter_id
    assert kw["cycle_id"] == cycle_id
    assert len(kw["error_hash"]) == 8
    assert kw["error_type"] == "RuntimeError"


async def test_discord_not_called_when_url_is_none(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """_post_discord must not be called when discord_webhook_url=None."""
    season_id, chapter_id, cycle_id = await _setup_cycle_in_state(
        session, "FILTERING", "no-disc-001"
    )

    with _RegistrySnapshot():
        side_effects.register("_test_crash_nodisc", _crash_fn)

        with patch.object(
            safe_side_effect,
            "_post_discord",
            new_callable=AsyncMock,
        ) as mock_discord:
            try:
                await run_safe(
                    name="_test_crash_nodisc",
                    chapter_id=chapter_id,
                    cycle_id=cycle_id,
                    session_factory=session_factory,
                    discord_webhook_url=None,   # no URL
                )
            finally:
                await _restore_kill_switch(session_factory)
                await _cleanup(session, season_id)

    mock_discord.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests — graceful degradation
# ---------------------------------------------------------------------------


class _BrokenAsyncCM:
    """Async context manager that raises on entry — simulates DB failure."""

    async def __aenter__(self) -> NoReturn:
        raise RuntimeError("simulated DB failure inside _handle_failure")

    async def __aexit__(self, *_: object) -> None:
        pass


async def test_handle_failure_db_error_does_not_propagate(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """If _handle_failure's DB ops fail, run_safe still returns normally.

    The inner exception is logged at CRITICAL but must not surface to the
    BackgroundTask runner as an unhandled exception.
    """
    season_id, chapter_id, cycle_id = await _setup_cycle_in_state(
        session, "FILTERING", "dbfail-001"
    )

    # A session_factory that always fails on __aenter__.
    broken_factory: async_sessionmaker[AsyncSession] = MagicMock(
        return_value=_BrokenAsyncCM()
    )

    with _RegistrySnapshot():
        side_effects.register("_test_crash_dbfail", _crash_fn)

        # Must complete without raising, even though _handle_failure fails.
        await run_safe(
            name="_test_crash_dbfail",
            chapter_id=chapter_id,
            cycle_id=cycle_id,
            session_factory=broken_factory,
            discord_webhook_url=None,
        )

    # Cycle state is still FILTERING (handle_failure never committed).
    row = await session.execute(
        sa.text("SELECT state FROM cycles WHERE id = :id"), {"id": cycle_id}
    )
    assert row.scalar_one() == "FILTERING"

    await _cleanup(session, season_id)
