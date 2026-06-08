"""Tests for the FastAPI application factory and the request_id middleware.

Module 001 / Task T-008.

Coverage:
  - create_app() returns a FastAPI instance with the expected title and version.
  - /openapi.json is reachable in dev/test and returns the documented schema.
  - X-Request-Id is auto-injected on every response (uuid4).
  - A client-supplied X-Request-Id is preserved end-to-end.
  - /docs and /redoc are 404 when ENV=prod, but /openapi.json still works.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import create_app
from app.middleware.request_id import REQUEST_ID_HEADER
from app.settings import get_settings

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TEST_ENV: dict[str, str] = {
    "ENV": "test",
    "LOG_LEVEL": "WARNING",
    "DATABASE_URL": "postgresql+asyncpg://app:app@localhost:5433/aiplottwist_test",
    "TICK_SECRET": "test-tick-secret",
    "JWT_SECRET": "test-jwt-secret",
}


def _apply_env(monkeypatch: pytest.MonkeyPatch, overrides: dict[str, str] | None = None) -> None:
    """Apply a known baseline of env vars plus optional overrides."""
    base = {**_TEST_ENV, **(overrides or {})}
    for key, value in base.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    """Build a FastAPI app under the test environment."""
    _apply_env(monkeypatch)
    try:
        yield create_app()
    finally:
        get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """TestClient wired to the app fixture (lifespan-aware)."""
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_app_can_be_created(app: FastAPI) -> None:
    """create_app() returns a configured FastAPI instance."""
    assert app.title == "AI Plot Twist API"
    assert app.version == "0.1.0"


def test_openapi_endpoint_returns_schema(client: TestClient) -> None:
    """/openapi.json returns the schema with the documented metadata."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    body = response.json()
    assert body["info"]["title"] == "AI Plot Twist API"
    assert body["info"]["version"] == "0.1.0"


def test_request_id_added_to_response(client: TestClient) -> None:
    """Every response carries a uuid4 X-Request-Id header."""
    response = client.get("/openapi.json")
    assert REQUEST_ID_HEADER in response.headers
    # Validate uuid4 shape — this raises on malformed input.
    uuid.UUID(response.headers[REQUEST_ID_HEADER], version=4)


def test_client_provided_request_id_is_preserved(client: TestClient) -> None:
    """A client-supplied X-Request-Id is honored and echoed back unchanged."""
    custom_id = "trace-from-edge-12345"
    response = client.get("/openapi.json", headers={REQUEST_ID_HEADER: custom_id})
    assert response.headers[REQUEST_ID_HEADER] == custom_id


def test_docs_disabled_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    """Interactive docs are 404 in prod; /openapi.json is still public."""
    _apply_env(monkeypatch, overrides={"ENV": "prod"})
    try:
        prod_app = create_app()
        with TestClient(prod_app) as prod_client:
            assert prod_client.get("/docs").status_code == 404
            assert prod_client.get("/redoc").status_code == 404
            assert prod_client.get("/openapi.json").status_code == 200
    finally:
        get_settings.cache_clear()
