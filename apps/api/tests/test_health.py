"""Tests for ``GET /healthz``.

Module 001 / Task T-010.

Coverage:
  - healthy: returns 200 with the documented payload (skip if no real DB).
  - DB unreachable: returns 503 with the documented error payload
    (forced via dependency override → broken engine).
  - response body never leaks exception text, secrets, or internal paths
    (Gate 9 — defense in depth).
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db import dispose_engine, get_engine
from app.main import create_app
from app.settings import get_settings
from tests.conftest import _is_placeholder_database_url

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    """Build a fresh FastAPI app with test env vars."""
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("TICK_SECRET", "test-tick-secret")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    # DATABASE_URL is inherited from conftest (placeholder unless the user
    # exported a real one).
    get_settings.cache_clear()
    try:
        yield create_app()
    finally:
        get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_healthz_returns_200_when_db_ok(client: TestClient) -> None:
    """Happy path — requires a reachable Postgres."""
    if _is_placeholder_database_url(os.environ.get("DATABASE_URL", "")):
        pytest.skip("DATABASE_URL no apunta a una base real.")
    # Re-prime the singleton in case a previous test disposed it.
    await dispose_engine()

    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok"}}


def test_healthz_returns_503_when_db_unreachable(app: FastAPI) -> None:
    """Force the engine to point at a closed port → 503."""
    # 127.0.0.1:1 has no listener; asyncpg fails fast with ConnectionError.
    broken_engine: AsyncEngine = create_async_engine(
        "postgresql+asyncpg://nope:nope@127.0.0.1:1/none"
    )
    app.dependency_overrides[get_engine] = lambda: broken_engine
    try:
        with TestClient(app) as broken_client:
            response = broken_client.get("/healthz")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"status": "error", "checks": {"database": "error"}}


def test_healthz_body_does_not_leak_internals(app: FastAPI) -> None:
    """Constitution Gate 9: error response is the documented shape, nothing else."""
    broken_engine: AsyncEngine = create_async_engine(
        "postgresql+asyncpg://nope:nope@127.0.0.1:1/none"
    )
    app.dependency_overrides[get_engine] = lambda: broken_engine
    try:
        with TestClient(app) as broken_client:
            response = broken_client.get("/healthz")
    finally:
        app.dependency_overrides.clear()

    # Exact body shape — nothing extra.
    assert response.json() == {"status": "error", "checks": {"database": "error"}}

    # Defensive: scan the raw text for known leak indicators.
    body_lower = response.text.lower()
    for forbidden in ("traceback", "exception", "asyncpg", "127.0.0.1", "nope", "password"):
        assert forbidden not in body_lower, f"forbidden leak in body: {forbidden!r}"
