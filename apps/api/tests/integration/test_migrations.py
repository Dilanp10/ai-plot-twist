"""Integration test: migration 0002 (users + invites) round-trip.

Module 002 / Task T-001.

Skips when DATABASE_URL is the conftest placeholder (no real DB available).
CI provides Postgres via a ``services:`` block.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import Connection, inspect
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command

API_DIR = Path(__file__).parent.parent.parent
ALEMBIC_INI = API_DIR / "alembic.ini"


def _alembic_config(database_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _make_has_table_checker(table_name: str) -> Callable[[Connection], bool]:
    def check(conn: Connection) -> bool:
        return inspect(conn).has_table(table_name)

    return check


async def _tables_exist(database_url: str, *table_names: str) -> dict[str, bool]:
    engine = create_async_engine(database_url)
    try:
        result: dict[str, bool] = {}
        async with engine.connect() as conn:
            for t in table_names:
                result[t] = await conn.run_sync(_make_has_table_checker(t))
        return result
    finally:
        await engine.dispose()


async def test_0002_upgrade_then_downgrade() -> None:
    """Round-trip upgrade/downgrade twice; users + invites appear and disappear cleanly."""
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

        present = await _tables_exist(database_url, "invites", "users")
        assert present["invites"], f"ciclo {cycle_idx}: invites debería existir tras upgrade"
        assert present["users"], f"ciclo {cycle_idx}: users debería existir tras upgrade"

        await asyncio.to_thread(command.downgrade, cfg, "base")

        gone = await _tables_exist(database_url, "invites", "users")
        assert not gone["invites"], (
            f"ciclo {cycle_idx}: invites debería estar borrada tras downgrade"
        )
        assert not gone["users"], (
            f"ciclo {cycle_idx}: users debería estar borrada tras downgrade"
        )

    # Dejar la DB en estado usable para tests subsiguientes.
    await asyncio.to_thread(command.upgrade, cfg, "head")
    final = await _tables_exist(database_url, "invites", "users")
    assert final["invites"] and final["users"]
