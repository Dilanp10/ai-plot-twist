"""Integration test: async engine can connect to Postgres and run SELECT 1.

Module 001 / Task T-007.

This test requires a live Postgres instance reachable at DATABASE_URL.
Locally: run `pnpm db:up` first (available after T-019).
In CI:   the workflow's `services: postgres:16` block provides the DB.

The test is skipped (not failed) when DATABASE_URL is not set or is empty,
so a clean checkout with no DB running does not break `pnpm test`.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def test_select_one() -> None:
    """Open a connection and execute SELECT 1; assert the result is 1."""
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        pytest.skip(
            "DATABASE_URL no está configurado. "
            "Iniciá la base de datos con `pnpm db:up` y "
            "configurá DATABASE_URL en .env.local."
        )

    # Create a fresh engine directly — does not touch the app singleton so
    # this test stays isolated even when get_settings() is cached.
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            value = result.scalar_one()
            assert value == 1
    finally:
        await engine.dispose()


async def test_get_session_yields_async_session() -> None:
    """get_session() is an async generator that yields an AsyncSession.

    This test only validates the shape of the dependency, not the DB connection.
    It also skips if no DATABASE_URL is available.
    """
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        pytest.skip("DATABASE_URL no está configurado.")

    # Import here so the env-var check above can skip before any engine is created.
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db import dispose_engine, get_session

    # Temporarily set DATABASE_URL in the process env so get_settings() can load.
    # (The env var is already set because we checked above.)
    sessions: list[AsyncSession] = []
    gen = get_session()
    session = await gen.__anext__()
    sessions.append(session)
    assert isinstance(session, AsyncSession)
    # Clean teardown: close the generator and dispose the singleton engine.
    try:
        await gen.aclose()
    finally:
        await dispose_engine()
