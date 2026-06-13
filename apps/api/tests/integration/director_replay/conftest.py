"""Fixtures local to the director-replay integration tests (T-011).

Provides:
  - ``database_url`` + ``_ensure_migrated``: skip if no real DB, run
    alembic upgrade head once per module.
  - ``session``: per-test AsyncSession against the real DB; tests are
    responsible for explicit cleanup (the endpoint commits per batch
    via run_director_filter, so rollback won't save us).
  - ``admin_token`` + ``app_factory``: build a FastAPI app whose
    ``app.state.director_router`` is filled by the test (when the test
    needs the router; the 503 test deliberately leaves it unset).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from alembic import command

_API_DIR = Path(__file__).parent.parent.parent.parent
_ALEMBIC_INI = _API_DIR / "alembic.ini"

ADMIN_TOKEN = "test-admin-token-T011"


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
    engine = create_async_engine(database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def admin_token(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[str]:
    """Set ADMIN_TOKEN env var and reset the cached Settings singleton."""
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN_TOKEN)

    # The settings module caches a Settings() instance with @lru_cache;
    # blow it away so the new env value is picked up.
    from app.settings import get_settings

    get_settings.cache_clear()
    yield ADMIN_TOKEN
    get_settings.cache_clear()


@pytest.fixture
def app_factory(
    admin_token: str,
    database_url: str,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., FastAPI]:
    """Return a builder for the FastAPI test app.

    Caller can pass ``director_router=<obj>`` to seed
    ``app.state.director_router`` before the first request, or omit it
    to exercise the 503 branch.

    Wires ``get_session`` to yield the test's fixture session, so the
    endpoint and the test share one AsyncSession (avoids the Fly-VM-
    style "Event loop is closed" teardown when the app's own engine
    disposes while the fixture session still holds a connection).
    """
    # Make sure the app's settings see the same URL the test does.
    monkeypatch.setenv("DATABASE_URL", database_url)
    from app.settings import get_settings

    get_settings.cache_clear()

    def _build(director_router: Any | None = None) -> FastAPI:
        from app.db import get_session
        from app.main import create_app

        app = create_app()
        if director_router is not None:
            app.state.director_router = director_router

        async def _override_session() -> AsyncIterator[AsyncSession]:
            yield session

        app.dependency_overrides[get_session] = _override_session
        return app

    return _build
