"""Tests for ``POST /api/v1/internal/transition`` — HMAC + payload validation.

Module 003 / Task T-015.

Covers only the middleware/validation layer (HMAC signature, payload shape).
Executor dispatch and failure-mode mapping are tested in
``tests/integration/test_transition_endpoint.py``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.domain.cycle_executor import TransitionResult
from app.domain.watchdog import WatchdogResult
from app.main import create_app
from app.middleware.hmac_tick import TICK_SIGNATURE_HEADER
from app.settings import get_settings

_TICK_SECRET = "test-tick-secret"
_TRANSITION_URL = "/api/v1/internal/transition"

_FAKE_RESULT = TransitionResult(
    status="applied",
    transition_id=1,
    applied_at=datetime(2026, 6, 9, 12, 0, 0, tzinfo=UTC),
    side_effect_name=None,
    cycle_id=1,
    chapter_id=1,
)

_FAKE_WATCHDOG = WatchdogResult(
    verdict="ready_for_release",
    cycle_id=1,
    cycle_state="PENDING_RELEASE",
    elapsed_seconds=100.0,
    forced_failed=False,
    discord_posted=False,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sign(body: bytes, secret: str = _TICK_SECRET) -> str:
    return base64.b64encode(
        hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("ascii")


def _make_body(
    *,
    to: str = "WATCHDOG",
    ts: int | None = None,
    trigger_id: str | None = None,
) -> bytes:
    payload: dict[str, object] = {
        "to": to,
        "ts": ts if ts is not None else int(time.time()),
        "trigger_id": trigger_id if trigger_id is not None else str(uuid.uuid4()),
    }
    return json.dumps(payload).encode("utf-8")


def _post(client: TestClient, body: bytes, *, signature: str | None = None) -> Response:
    sig = signature if signature is not None else _sign(body)
    return client.post(  # type: ignore[no-any-return]
        _TRANSITION_URL,
        content=body,
        headers={
            "Content-Type": "application/json",
            TICK_SIGNATURE_HEADER: sig,
        },
    )


# ---------------------------------------------------------------------------
# Mock DB session (avoids DB connection in unit tests)
# ---------------------------------------------------------------------------


async def _mock_session() -> AsyncSession:  # type: ignore[misc]
    yield AsyncMock(spec=AsyncSession)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("TICK_SECRET", _TICK_SECRET)
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://mock/mock")
    get_settings.cache_clear()

    a = create_app()
    # Override DB session so no real connection is attempted.
    a.dependency_overrides[get_session] = _mock_session
    try:
        yield a
    finally:
        get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Happy-path smoke (with mocked executor)
# ---------------------------------------------------------------------------


def test_valid_estreno_returns_202(client: TestClient) -> None:
    """A valid ESTRENO tick (executor mocked to return applied) → 202."""
    with patch(
        "app.api.internal_transition.executor_transition",
        new=AsyncMock(return_value=_FAKE_RESULT),
    ):
        body = _make_body(to="ESTRENO")
        response = _post(client, body)

    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "applied"


def test_valid_watchdog_returns_200(client: TestClient) -> None:
    """A valid WATCHDOG tick (watchdog mocked) → 200 with verdict."""
    with patch(
        "app.api.internal_transition.watchdog_check",
        new=AsyncMock(return_value=_FAKE_WATCHDOG),
    ):
        body = _make_body(to="WATCHDOG")
        response = _post(client, body)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "watchdog_ok"
    assert data["verdict"] == "ready_for_release"


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------


def test_invalid_to_returns_422(client: TestClient) -> None:
    body = _make_body(to="WRONG_STATE")
    response = _post(client, body)
    assert response.status_code == 422
    assert response.json()["code"] == "bad_payload"
    assert "to" in response.json()["detail"]


def test_missing_trigger_id_returns_422(client: TestClient) -> None:
    body = json.dumps({"to": "WATCHDOG", "ts": int(time.time())}).encode("utf-8")
    response = _post(client, body)
    assert response.status_code == 422
    assert response.json()["code"] == "bad_payload"
    assert "trigger_id" in response.json()["detail"]


def test_short_trigger_id_returns_422(client: TestClient) -> None:
    body = _make_body(trigger_id="abc")  # 3 chars; min is 4
    response = _post(client, body)
    assert response.status_code == 422
    assert response.json()["code"] == "bad_payload"


def test_long_trigger_id_returns_422(client: TestClient) -> None:
    body = _make_body(trigger_id="x" * 129)  # 129 chars; max is 128
    response = _post(client, body)
    assert response.status_code == 422
    assert response.json()["code"] == "bad_payload"


@pytest.mark.parametrize(
    "to_value",
    ["ESTRENO", "FILTERING", "GENERACION", "WATCHDOG"],
)
def test_all_four_cron_values_accepted(client: TestClient, to_value: str) -> None:
    """All four cron `to` values pass semantic validation."""
    with (
        patch(
            "app.api.internal_transition.executor_transition",
            new=AsyncMock(return_value=_FAKE_RESULT),
        ),
        patch(
            "app.api.internal_transition.watchdog_check",
            new=AsyncMock(return_value=_FAKE_WATCHDOG),
        ),
    ):
        body = _make_body(to=to_value)
        response = _post(client, body)

    assert response.status_code in (200, 202)


# ---------------------------------------------------------------------------
# HMAC / middleware (unchanged from stub tests)
# ---------------------------------------------------------------------------


def test_bad_hmac_returns_401_end_to_end(client: TestClient) -> None:
    """HMAC dependency is wired up on this route."""
    body = _make_body()
    response = _post(client, body, signature="not-a-real-signature")
    assert response.status_code == 401
    assert response.json()["code"] == "bad_hmac"
