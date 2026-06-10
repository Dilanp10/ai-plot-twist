"""Unit tests: POST /api/v1/internal/kill-switch.

Module 003 / Task T-017.

``verify_admin_token`` is bypassed via ``dependency_overrides``; the DB session
is replaced with an AsyncMock; ``SystemFlagsRepo`` is patched at the module
level so no real DB connection is made.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.infra.system_flags_repo import FlagValue
from app.main import create_app
from app.middleware.admin_token import verify_admin_token
from app.settings import get_settings

_URL = "/api/v1/internal/kill-switch"
_ADMIN_TOKEN = "test-admin-token"

_NOW = datetime(2026, 6, 10, 0, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _mock_session() -> AsyncSession:  # type: ignore[misc]
    yield AsyncMock(spec=AsyncSession)


def _flag(on: bool, reason: str | None = None) -> FlagValue:
    return FlagValue(
        flag_key="kill_switch",
        flag_value={"on": on, "reason": reason},
        updated_by="admin",
        updated_at=_NOW,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("TICK_SECRET", "test-tick-secret")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://mock/mock")
    monkeypatch.setenv("ADMIN_TOKEN", _ADMIN_TOKEN)
    get_settings.cache_clear()

    a = create_app()
    a.dependency_overrides[get_session] = _mock_session
    a.dependency_overrides[verify_admin_token] = lambda: None  # bypass auth
    try:
        yield a
    finally:
        get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Happy-path: turn on
# ---------------------------------------------------------------------------


def test_turn_on_returns_200_active(client: TestClient) -> None:
    with patch(
        "app.api.internal_kill_switch.SystemFlagsRepo"
    ) as MockRepo:
        MockRepo.return_value.set = AsyncMock(return_value=_flag(on=True, reason="rebuild"))
        r = client.post(_URL, json={"on": True, "reason": "rebuild"})

    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "kill_switch_active"
    assert data["reason"] == "rebuild"


# ---------------------------------------------------------------------------
# Happy-path: turn off
# ---------------------------------------------------------------------------


def test_turn_off_returns_200_inactive(client: TestClient) -> None:
    with patch(
        "app.api.internal_kill_switch.SystemFlagsRepo"
    ) as MockRepo:
        MockRepo.return_value.set = AsyncMock(return_value=_flag(on=False))
        r = client.post(_URL, json={"on": False})

    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "kill_switch_inactive"
    assert data["reason"] is None


# ---------------------------------------------------------------------------
# reason is optional (None)
# ---------------------------------------------------------------------------


def test_reason_none_is_allowed(client: TestClient) -> None:
    with patch(
        "app.api.internal_kill_switch.SystemFlagsRepo"
    ) as MockRepo:
        MockRepo.return_value.set = AsyncMock(return_value=_flag(on=True))
        r = client.post(_URL, json={"on": True})

    assert r.status_code == 200
    assert r.json()["reason"] is None


# ---------------------------------------------------------------------------
# repo.set called with correct arguments
# ---------------------------------------------------------------------------


def test_repo_set_called_with_correct_args(client: TestClient) -> None:
    with patch(
        "app.api.internal_kill_switch.SystemFlagsRepo"
    ) as MockRepo:
        mock_instance = MockRepo.return_value
        mock_instance.set = AsyncMock(return_value=_flag(on=True, reason="test"))
        client.post(_URL, json={"on": True, "reason": "test"})

    mock_instance.set.assert_awaited_once_with(
        key="kill_switch",
        value={"on": True, "reason": "test"},
        updated_by="admin",
    )


# ---------------------------------------------------------------------------
# Auth guard — no override → 401/403 without valid token
# ---------------------------------------------------------------------------


def test_no_auth_header_returns_401(app: FastAPI) -> None:
    """Without the auth bypass, missing header → 401."""
    del app.dependency_overrides[verify_admin_token]
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post(_URL, json={"on": True})
    assert r.status_code == 401
    assert r.json()["code"] == "missing_admin_token"


def test_wrong_token_returns_403(app: FastAPI) -> None:
    """Without the auth bypass, wrong token → 403."""
    del app.dependency_overrides[verify_admin_token]
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.post(
            _URL,
            json={"on": True},
            headers={"Authorization": "Bearer wrong-token"},
        )
    assert r.status_code == 403
    assert r.json()["code"] == "bad_admin_token"


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------


def test_missing_on_field_returns_422(client: TestClient) -> None:
    r = client.post(_URL, json={"reason": "oops"})
    assert r.status_code == 422


def test_non_bool_on_returns_422(client: TestClient) -> None:
    # Pydantic v2 coerces strings like "yes"/"true" → True in lax mode,
    # but a list/object is never valid for a bool field.
    r = client.post(_URL, json={"on": [1, 2, 3]})
    assert r.status_code == 422
