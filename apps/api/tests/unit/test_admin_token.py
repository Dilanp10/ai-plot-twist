"""Unit tests: verify_admin_token dependency.

Module 003 / Task T-016.

Tests the three failure modes and the success path using a minimal FastAPI
app with a single test route that depends on ``verify_admin_token``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.errors import ProblemDetail, problem_handler
from app.middleware.admin_token import verify_admin_token
from app.settings import Settings, get_settings

_ADMIN_TOKEN = "super-secret-admin-token-abc123"


# ---------------------------------------------------------------------------
# Test app setup
# ---------------------------------------------------------------------------


def _make_app(admin_token: str | None = _ADMIN_TOKEN) -> FastAPI:
    """Build a minimal FastAPI app with a single protected endpoint."""
    app = FastAPI()
    app.add_exception_handler(ProblemDetail, problem_handler)

    settings = Settings(
        database_url="postgresql+asyncpg://mock/mock",
        jwt_secret="test-jwt",
        env="test",
        admin_token=admin_token,
    )
    app.dependency_overrides[get_settings] = lambda: settings

    @app.get("/protected")
    async def protected(_: None = Depends(verify_admin_token)) -> dict[str, str]:
        return {"ok": "true"}

    return app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(_make_app(), raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def unconfigured_client() -> Iterator[TestClient]:
    """App without ADMIN_TOKEN configured."""
    with TestClient(_make_app(admin_token=None), raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# 503 — ADMIN_TOKEN not configured
# ---------------------------------------------------------------------------


def test_missing_server_config_returns_503(unconfigured_client: TestClient) -> None:
    r = unconfigured_client.get(
        "/protected",
        headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
    )
    assert r.status_code == 503
    data = r.json()
    assert data["code"] == "admin_token_missing"
    assert data["status"] == 503


# ---------------------------------------------------------------------------
# 401 — Authorization header absent or malformed
# ---------------------------------------------------------------------------


def test_no_auth_header_returns_401(client: TestClient) -> None:
    r = client.get("/protected")
    assert r.status_code == 401
    data = r.json()
    assert data["code"] == "missing_admin_token"


def test_wrong_scheme_returns_401(client: TestClient) -> None:
    r = client.get("/protected", headers={"Authorization": f"Token {_ADMIN_TOKEN}"})
    assert r.status_code == 401
    assert r.json()["code"] == "missing_admin_token"


def test_bearer_without_space_returns_401(client: TestClient) -> None:
    r = client.get("/protected", headers={"Authorization": f"Bearer{_ADMIN_TOKEN}"})
    assert r.status_code == 401
    assert r.json()["code"] == "missing_admin_token"


# ---------------------------------------------------------------------------
# 403 — Wrong token
# ---------------------------------------------------------------------------


def test_wrong_token_returns_403(client: TestClient) -> None:
    r = client.get(
        "/protected", headers={"Authorization": "Bearer wrong-token-here"}
    )
    assert r.status_code == 403
    data = r.json()
    assert data["code"] == "bad_admin_token"
    assert data["status"] == 403


def test_empty_token_returns_403(client: TestClient) -> None:
    """``Authorization: Bearer `` (empty token after space) → 403."""
    r = client.get("/protected", headers={"Authorization": "Bearer "})
    assert r.status_code == 403
    assert r.json()["code"] == "bad_admin_token"


# ---------------------------------------------------------------------------
# 200 — Correct token
# ---------------------------------------------------------------------------


def test_correct_token_returns_200(client: TestClient) -> None:
    r = client.get(
        "/protected", headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"}
    )
    assert r.status_code == 200
    assert r.json() == {"ok": "true"}


def test_response_is_problem_json_on_error(client: TestClient) -> None:
    """Error responses carry ``application/problem+json`` content-type."""
    r = client.get("/protected")
    assert "problem+json" in r.headers.get("content-type", "")
