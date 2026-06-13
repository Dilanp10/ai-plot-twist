"""Pytest fixtures local to director_filter integration tests.

Module 006 / Task T-009.

Defining the fixtures here (instead of importing them into each test
file) avoids the F811 redefinition clash between the imported fixture
and the test parameter.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from alembic import command

_API_DIR = Path(__file__).parent.parent.parent.parent
_ALEMBIC_INI = _API_DIR / "alembic.ini"


def _alembic_cfg(database_url: str) -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_API_DIR / "alembic"))
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
    asyncio.get_event_loop().run_until_complete(
        asyncio.to_thread(
            command.upgrade, _alembic_cfg(database_url), "head"
        )
    )


@pytest.fixture
async def session(database_url: str) -> AsyncIterator[AsyncSession]:
    """Per-test async session.

    The director's filter commits per batch, so this fixture does NOT
    rollback on teardown — each test is responsible for explicit cleanup
    via the helper module's ``cleanup`` function.
    """
    engine = create_async_engine(database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    async with factory() as s:
        yield s
    await engine.dispose()
