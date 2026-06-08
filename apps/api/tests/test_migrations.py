"""Integration test: Alembic upgrade/downgrade round-trip is reversible.

Module 001 / Task T-009.

Runs ``alembic upgrade head`` then ``downgrade base`` twice and asserts that
the ``idempotency_keys`` table appears and disappears cleanly each time —
verifying the migration is idempotent under replays.

Skips if no DATABASE_URL is configured (a clean checkout with no DB running
must not break ``pnpm test``). The CI workflow provides Postgres via a
``services:`` block.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command

API_DIR = Path(__file__).parent.parent
ALEMBIC_INI = API_DIR / "alembic.ini"


def _alembic_config(database_url: str) -> Config:
    """Build a Config that runs Alembic regardless of the test's cwd."""
    cfg = Config(str(ALEMBIC_INI))
    # ``script_location`` is relative to the .ini file's directory; make it
    # absolute so the alembic command works even when pytest is launched from
    # the repo root.
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


async def _table_exists(database_url: str, table_name: str) -> bool:
    """Return True iff *table_name* exists in the DB at *database_url*."""
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table(table_name))
    finally:
        await engine.dispose()


async def test_upgrade_then_downgrade_twice() -> None:
    """Round-trip upgrade/downgrade twice; table appears and disappears cleanly."""
    from tests.conftest import _is_placeholder_database_url

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url or _is_placeholder_database_url(database_url):
        pytest.skip(
            "DATABASE_URL no apunta a una base real. "
            "Levantá Postgres (`pnpm db:up`) y exportá la URL para correr este test."
        )

    cfg = _alembic_config(database_url)

    for cycle_idx in range(2):
        await asyncio.to_thread(command.upgrade, cfg, "head")
        assert await _table_exists(database_url, "idempotency_keys"), (
            f"ciclo {cycle_idx}: idempotency_keys debería existir tras upgrade"
        )

        await asyncio.to_thread(command.downgrade, cfg, "base")
        assert not await _table_exists(database_url, "idempotency_keys"), (
            f"ciclo {cycle_idx}: idempotency_keys debería estar borrada tras downgrade"
        )

    # Leave the DB in a usable state for downstream tests.
    await asyncio.to_thread(command.upgrade, cfg, "head")
    assert await _table_exists(database_url, "idempotency_keys")
