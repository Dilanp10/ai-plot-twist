"""Integration tests: migration round-trips for modules 002 and 003.

Module 002 / Tasks T-001, T-002.
Module 003 / Tasks T-001, T-002.

Skips when DATABASE_URL is the conftest placeholder (no real DB available).
CI provides Postgres via a ``services:`` block.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy import Connection, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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


async def test_0003_upgrade_then_downgrade() -> None:
    """Round-trip upgrade/downgrade twice; rate_limit_buckets appears and disappears cleanly."""
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

        present = await _tables_exist(database_url, "rate_limit_buckets")
        assert present["rate_limit_buckets"], (
            f"ciclo {cycle_idx}: rate_limit_buckets debería existir tras upgrade"
        )

        await asyncio.to_thread(command.downgrade, cfg, "base")

        gone = await _tables_exist(database_url, "rate_limit_buckets")
        assert not gone["rate_limit_buckets"], (
            f"ciclo {cycle_idx}: rate_limit_buckets debería estar borrada tras downgrade"
        )

    # Dejar la DB en estado usable para tests subsiguientes.
    await asyncio.to_thread(command.upgrade, cfg, "head")
    final = await _tables_exist(database_url, "rate_limit_buckets")
    assert final["rate_limit_buckets"]


# ---------------------------------------------------------------------------
# Module 003 — T-001: seasons + chapters (0004)
# ---------------------------------------------------------------------------


async def test_0004_upgrade_then_downgrade() -> None:
    """Round-trip x2 for 0004: seasons + chapters appear and disappear cleanly."""
    from tests.conftest import _is_placeholder_database_url

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url or _is_placeholder_database_url(database_url):
        pytest.skip("DATABASE_URL no apunta a una base real.")

    cfg = _alembic_config(database_url)

    for cycle_idx in range(2):
        await asyncio.to_thread(command.upgrade, cfg, "head")

        present = await _tables_exist(database_url, "seasons", "chapters")
        assert present["seasons"], f"ciclo {cycle_idx}: seasons debería existir tras upgrade"
        assert present["chapters"], f"ciclo {cycle_idx}: chapters debería existir tras upgrade"

        await asyncio.to_thread(command.downgrade, cfg, "base")

        gone = await _tables_exist(database_url, "seasons", "chapters")
        assert not gone["seasons"], (
            f"ciclo {cycle_idx}: seasons debería estar borrada tras downgrade"
        )
        assert not gone["chapters"], (
            f"ciclo {cycle_idx}: chapters debería estar borrada tras downgrade"
        )

    # Leave DB ready for subsequent tests.
    await asyncio.to_thread(command.upgrade, cfg, "head")
    final = await _tables_exist(database_url, "seasons", "chapters")
    assert final["seasons"] and final["chapters"]


# ---------------------------------------------------------------------------
# Module 003 — T-002: cycles + state_transitions (0005)
# ---------------------------------------------------------------------------


async def test_0005_upgrade_then_downgrade() -> None:
    """Round-trip x2 for 0005: cycles + state_transitions appear and disappear."""
    from tests.conftest import _is_placeholder_database_url

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url or _is_placeholder_database_url(database_url):
        pytest.skip("DATABASE_URL no apunta a una base real.")

    cfg = _alembic_config(database_url)

    for cycle_idx in range(2):
        await asyncio.to_thread(command.upgrade, cfg, "head")

        present = await _tables_exist(
            database_url, "cycles", "state_transitions"
        )
        assert present["cycles"], (
            f"ciclo {cycle_idx}: cycles debería existir tras upgrade"
        )
        assert present["state_transitions"], (
            f"ciclo {cycle_idx}: state_transitions debería existir tras upgrade"
        )

        await asyncio.to_thread(command.downgrade, cfg, "base")

        gone = await _tables_exist(database_url, "cycles", "state_transitions")
        assert not gone["cycles"], (
            f"ciclo {cycle_idx}: cycles debería estar borrada tras downgrade"
        )
        assert not gone["state_transitions"], (
            f"ciclo {cycle_idx}: state_transitions debería estar borrada tras downgrade"
        )

    # Leave DB ready for subsequent tests.
    await asyncio.to_thread(command.upgrade, cfg, "head")
    final = await _tables_exist(database_url, "cycles", "state_transitions")
    assert final["cycles"] and final["state_transitions"]


async def test_0005_uniq_st_trigger_enforced() -> None:
    """Inserting the same (cycle_id, to_state, trigger_id) twice raises IntegrityError.

    Verifies that the partial UNIQUE index ``uniq_st_trigger`` is present and
    active after the 0005 upgrade.
    """
    from tests.conftest import _is_placeholder_database_url

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url or _is_placeholder_database_url(database_url):
        pytest.skip("DATABASE_URL no apunta a una base real.")

    cfg = _alembic_config(database_url)
    await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )

    season_id: int
    cycle_id: int

    try:
        async with factory() as s:
            # Minimal season
            await s.execute(
                sa.text(
                    "INSERT INTO seasons (slug, title, bible_json, started_on) "
                    "VALUES ('_mig-test-s99', 'Mig Test', '{}', CURRENT_DATE)"
                )
            )
            row = await s.execute(
                sa.text("SELECT id FROM seasons WHERE slug = '_mig-test-s99'")
            )
            season_id = int(row.scalar_one())

            # Minimal chapter
            await s.execute(
                sa.text(
                    "INSERT INTO chapters "
                    "(season_id, day_index, title, synopsis, manifest_json, status) "
                    "VALUES (:sid, 1, 'T', 'S', '{\"panels\":[]}', 'ready')"
                ),
                {"sid": season_id},
            )
            chapter_id = int(
                (
                    await s.execute(
                        sa.text(
                            "SELECT id FROM chapters WHERE season_id = :sid"
                        ),
                        {"sid": season_id},
                    )
                ).scalar_one()
            )

            # Minimal cycle
            await s.execute(
                sa.text(
                    "INSERT INTO cycles "
                    "(season_id, chapter_id, state, cycle_date) "
                    "VALUES (:sid, :cid, 'PENDING_RELEASE', CURRENT_DATE)"
                ),
                {"sid": season_id, "cid": chapter_id},
            )
            cycle_id = int(
                (
                    await s.execute(
                        sa.text(
                            "SELECT id FROM cycles WHERE season_id = :sid"
                        ),
                        {"sid": season_id},
                    )
                ).scalar_one()
            )

            # First insert succeeds
            await s.execute(
                sa.text(
                    "INSERT INTO state_transitions "
                    "(cycle_id, from_state, to_state, triggered_by, trigger_id) "
                    "VALUES (:cid, 'PENDING_RELEASE', 'ESTRENO', 'cron', 'mig-test-tid-001')"
                ),
                {"cid": cycle_id},
            )
            await s.commit()

        # Second insert with identical (cycle_id, to_state, trigger_id) must conflict.
        with pytest.raises(IntegrityError):
            async with factory() as s:
                await s.execute(
                    sa.text(
                        "INSERT INTO state_transitions "
                        "(cycle_id, from_state, to_state, triggered_by, trigger_id) "
                        "VALUES (:cid, 'PENDING_RELEASE', 'ESTRENO', 'cron', 'mig-test-tid-001')"
                    ),
                    {"cid": cycle_id},
                )
                await s.commit()

    finally:
        # Cleanup — cascade deletes handle state_transitions + chapters
        async with factory() as s:
            await s.execute(
                sa.text("DELETE FROM cycles WHERE season_id = :sid"),
                {"sid": season_id},
            )
            await s.execute(
                sa.text("DELETE FROM chapters WHERE season_id = :sid"),
                {"sid": season_id},
            )
            await s.execute(
                sa.text("DELETE FROM seasons WHERE id = :sid"),
                {"sid": season_id},
            )
            await s.commit()
        await engine.dispose()
