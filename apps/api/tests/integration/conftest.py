"""Shared fixtures for integration tests (module 002).

Provides:
  db_session       — function-scoped AsyncSession; rolls back after each test.
  unused_invite    — re-exported from tests.fixtures.invites
  redeemed_invite  — re-exported from tests.fixtures.invites
  active_user      — re-exported from tests.fixtures.users
  banned_user      — re-exported from tests.fixtures.users
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.fixtures import require_real_db_url

# Re-export fixture functions so pytest discovers them for all integration tests.
from tests.fixtures.invites import redeemed_invite, unused_invite  # noqa: F401
from tests.fixtures.users import active_user, banned_user  # noqa: F401


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Per-test AsyncSession that rolls back on teardown (no side-effects)."""
    url = require_real_db_url()
    engine = create_async_engine(url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    async with factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()
