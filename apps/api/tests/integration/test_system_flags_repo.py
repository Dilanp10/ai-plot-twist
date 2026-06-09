"""Integration tests: SystemFlagsRepo.

Module 003 / Task T-011.

Skips when DATABASE_URL is the conftest placeholder (no real DB available).

Tests verify:
  - get() reads the kill_switch seed row from migration 0006.
  - set() upserts correctly (new key + overwrite existing).
  - is_kill_switch_on() maps flag_value["on"] correctly.
  - Cache is populated on get() and invalidated on set().
  - clear_cache() flushes entries.

Each test calls clear_cache() before use to guarantee a fresh cache state.
The kill_switch row is restored to {on: false} in finally blocks to leave
the DB in a clean state for subsequent test runs.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.infra.system_flags_repo import (
    _TTL_SECONDS,
    FlagValue,
    SystemFlagsRepo,
    _cache,
    clear_cache,
)

API_DIR = Path(__file__).parent.parent.parent
ALEMBIC_INI = API_DIR / "alembic.ini"


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
def _fresh_cache() -> None:
    """Flush in-process cache before every test."""
    clear_cache()


# ---------------------------------------------------------------------------
# get()
# ---------------------------------------------------------------------------


async def test_get_returns_kill_switch_seed_row(session: AsyncSession) -> None:
    """Migration 0006 seeds kill_switch with on=false; get() returns it."""
    repo = SystemFlagsRepo(session)
    flag = await repo.get("kill_switch")
    assert flag is not None
    assert isinstance(flag, FlagValue)
    assert flag.flag_key == "kill_switch"
    assert flag.flag_value.get("on") is False
    assert flag.updated_by == "migration"


async def test_get_returns_none_for_missing_key(session: AsyncSession) -> None:
    repo = SystemFlagsRepo(session)
    result = await repo.get("__nonexistent_flag__")
    assert result is None


async def test_get_populates_cache(session: AsyncSession) -> None:
    """After get(), the value is stored in _cache."""
    assert "kill_switch" not in _cache
    repo = SystemFlagsRepo(session)
    await repo.get("kill_switch")
    assert "kill_switch" in _cache
    cached_value, expires_at = _cache["kill_switch"]
    assert cached_value.flag_key == "kill_switch"
    assert expires_at > 0


async def test_get_cache_ttl_is_30s(session: AsyncSession) -> None:
    """Cache entry expires at approximately now + _TTL_SECONDS."""
    import time

    repo = SystemFlagsRepo(session)
    before = time.monotonic()
    await repo.get("kill_switch")
    after = time.monotonic()

    _, expires_at = _cache["kill_switch"]
    # expires_at should be ≈ time of call + 30 s
    assert expires_at >= before + _TTL_SECONDS - 0.1
    assert expires_at <= after + _TTL_SECONDS + 0.1


async def test_get_uses_cache_on_second_call(session: AsyncSession) -> None:
    """Second get() for same key returns the cached FlagValue (same object)."""
    repo = SystemFlagsRepo(session)
    first = await repo.get("kill_switch")
    # Manually corrupt the DB value to verify cache is used.
    await session.execute(
        sa.text(
            "UPDATE system_flags SET flag_value = '{\"on\": true}'::jsonb "
            "WHERE flag_key = 'kill_switch'"
        )
    )
    # Do NOT commit — or rollback to leave DB clean.
    # Second get() should still return the cached (old) value.
    second = await repo.get("kill_switch")
    assert first is not None and second is not None
    assert second.flag_value.get("on") is False  # cache, not the DB update
    # Rollback the dirty write.
    await session.rollback()


# ---------------------------------------------------------------------------
# set()
# ---------------------------------------------------------------------------


async def test_set_creates_new_flag(session: AsyncSession) -> None:
    """set() on a nonexistent key creates the row."""
    repo = SystemFlagsRepo(session)
    key = "_test_new_flag_001"
    try:
        flag = await repo.set(key, {"enabled": True}, updated_by="test")
        await session.commit()
        assert flag.flag_key == key
        assert flag.flag_value == {"enabled": True}
        assert flag.updated_by == "test"
        assert flag.updated_at is not None
    finally:
        await session.execute(
            sa.text("DELETE FROM system_flags WHERE flag_key = :k"), {"k": key}
        )
        await session.commit()


async def test_set_updates_existing_flag(session: AsyncSession) -> None:
    """set() on kill_switch updates flag_value and updated_by."""
    repo = SystemFlagsRepo(session)
    try:
        updated = await repo.set(
            "kill_switch",
            {"on": True, "reason": "test"},
            updated_by="test-suite",
        )
        await session.commit()
        assert updated.flag_value == {"on": True, "reason": "test"}
        assert updated.updated_by == "test-suite"
    finally:
        # Restore seed state.
        await repo.set("kill_switch", {"on": False, "reason": None}, updated_by="migration")
        await session.commit()


async def test_set_invalidates_cache(session: AsyncSession) -> None:
    """set() removes the key from _cache so next get() fetches from DB."""
    repo = SystemFlagsRepo(session)
    # Warm the cache.
    await repo.get("kill_switch")
    assert "kill_switch" in _cache

    try:
        await repo.set("kill_switch", {"on": True, "reason": "inv-test"}, updated_by="t")
        await session.commit()
        # Cache must be gone after set().
        assert "kill_switch" not in _cache
    finally:
        await repo.set("kill_switch", {"on": False, "reason": None}, updated_by="migration")
        await session.commit()


async def test_set_returns_flag_with_correct_fields(session: AsyncSession) -> None:
    key = "_test_fields_001"
    repo = SystemFlagsRepo(session)
    try:
        flag = await repo.set(key, {"x": 1, "y": [1, 2]}, updated_by="qa")
        await session.commit()
        assert isinstance(flag, FlagValue)
        assert flag.flag_key == key
        assert flag.flag_value == {"x": 1, "y": [1, 2]}
        assert flag.updated_by == "qa"
    finally:
        await session.execute(
            sa.text("DELETE FROM system_flags WHERE flag_key = :k"), {"k": key}
        )
        await session.commit()


# ---------------------------------------------------------------------------
# is_kill_switch_on()
# ---------------------------------------------------------------------------


async def test_is_kill_switch_on_returns_false_by_default(
    session: AsyncSession,
) -> None:
    """Seed row has on=false → is_kill_switch_on() is False."""
    repo = SystemFlagsRepo(session)
    assert await repo.is_kill_switch_on() is False


async def test_is_kill_switch_on_returns_true_when_set(
    session: AsyncSession,
) -> None:
    repo = SystemFlagsRepo(session)
    try:
        await repo.set("kill_switch", {"on": True, "reason": "test"}, updated_by="t")
        await session.commit()
        clear_cache()  # force DB read
        assert await repo.is_kill_switch_on() is True
    finally:
        await repo.set("kill_switch", {"on": False, "reason": None}, updated_by="migration")
        await session.commit()


# ---------------------------------------------------------------------------
# clear_cache()
# ---------------------------------------------------------------------------


async def test_clear_cache_empties_all_entries(session: AsyncSession) -> None:
    repo = SystemFlagsRepo(session)
    await repo.get("kill_switch")
    assert len(_cache) > 0
    clear_cache()
    assert len(_cache) == 0


def test_ttl_constant_is_30_seconds() -> None:
    """_TTL_SECONDS is importable and equals 30."""
    assert _TTL_SECONDS == 30.0
