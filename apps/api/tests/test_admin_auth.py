"""Tests — Module 014 T-001: admin auth endpoint + JWT helpers."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain.admin_auth import (
    issue_admin_jwt,
    verify_admin_jwt,
    verify_admin_password,
)
from app.main import create_app
from app.settings import Settings, get_settings


# ---------------------------------------------------------------------------
# Unit tests — verify_admin_password
# ---------------------------------------------------------------------------


def _settings(password: str | None) -> Settings:
    return Settings(  # type: ignore[call-arg]
        database_url="postgresql+asyncpg://x:x@localhost/x",
        jwt_secret="test-secret",
        admin_password=password,
    )


def test_verify_admin_password_correct() -> None:
    assert verify_admin_password("dilan", _settings("dilan")) is True


def test_verify_admin_password_wrong() -> None:
    assert verify_admin_password("wrong", _settings("dilan")) is False


def test_verify_admin_password_empty_input() -> None:
    assert verify_admin_password("", _settings("dilan")) is False


def test_verify_admin_password_not_configured() -> None:
    assert verify_admin_password("dilan", _settings(None)) is False


# ---------------------------------------------------------------------------
# Unit tests — issue_admin_jwt + verify_admin_jwt
# ---------------------------------------------------------------------------


def test_issue_and_verify_admin_jwt() -> None:
    token = issue_admin_jwt("test-secret")
    assert verify_admin_jwt(token, "test-secret") is True


def test_verify_admin_jwt_wrong_secret() -> None:
    token = issue_admin_jwt("test-secret")
    assert verify_admin_jwt(token, "other-secret") is False


def test_verify_admin_jwt_garbage() -> None:
    assert verify_admin_jwt("not.a.jwt", "test-secret") is False


def test_verify_admin_jwt_user_token_rejected() -> None:
    """A regular user JWT must not pass admin verification."""
    import jwt as pyjwt
    from datetime import UTC, datetime, timedelta

    user_token = pyjwt.encode(
        {
            "sub": "some-uuid",
            "aud": "aiplottwist",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(days=90),
        },
        "test-secret",
        algorithm="HS256",
    )
    assert verify_admin_jwt(user_token, "test-secret") is False


# ---------------------------------------------------------------------------
# Integration tests — POST /api/v1/admin/auth
# ---------------------------------------------------------------------------


@pytest.fixture()
def client_with_password() -> TestClient:
    settings = _settings("dilan")
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


@pytest.fixture()
def client_no_password() -> TestClient:
    settings = _settings(None)
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def test_admin_auth_success(client_with_password: TestClient) -> None:
    resp = client_with_password.post(
        "/api/v1/admin/auth", json={"password": "dilan"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    # Token must be a valid admin JWT
    assert verify_admin_jwt(data["token"], "test-secret") is True


def test_admin_auth_wrong_password(client_with_password: TestClient) -> None:
    resp = client_with_password.post(
        "/api/v1/admin/auth", json={"password": "equivocada"}
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "wrong_admin_password"


def test_admin_auth_not_configured(client_no_password: TestClient) -> None:
    resp = client_no_password.post(
        "/api/v1/admin/auth", json={"password": "dilan"}
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "admin_password_not_configured"


# ---------------------------------------------------------------------------
# Integration tests — require_admin_jwt dependency
# ---------------------------------------------------------------------------


def test_admin_cycle_no_token(client_with_password: TestClient) -> None:
    resp = client_with_password.get("/api/v1/admin/cycle")
    assert resp.status_code == 401
    assert resp.json()["code"] == "missing_admin_token"


def test_admin_cycle_bad_token(client_with_password: TestClient) -> None:
    resp = client_with_password.get(
        "/api/v1/admin/cycle",
        headers={"Authorization": "Bearer garbage.token.here"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "bad_admin_token"


def test_admin_cycle_valid_token(client_with_password: TestClient) -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    token = issue_admin_jwt("test-secret")
    # Mock CyclesRepo so no DB connection is needed; 404 proves the token passed auth.
    with patch("app.api.admin.CyclesRepo") as mock_repo_cls:
        mock_repo = MagicMock()
        mock_repo.get_active = AsyncMock(return_value=None)
        mock_repo_cls.return_value = mock_repo
        resp = client_with_password.get(
            "/api/v1/admin/cycle",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404  # no cycle, but auth passed
