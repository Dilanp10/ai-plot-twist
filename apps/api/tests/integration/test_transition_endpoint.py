"""Integration tests: ``POST /api/v1/internal/transition`` — all six failure modes.

Module 003 / Task T-015.

Uses the FastAPI ``TestClient`` with:
  - ``dependency_overrides[get_session]`` → mock AsyncSession (no DB needed)
  - ``patch("app.api.internal_transition.executor_transition", ...)`` → each failure

Tests the HTTP-layer mapping of domain exceptions to RFC 7807 responses and
the happy-path 202/200 responses.

Six failure modes (from spec):
  1. KillSwitchActive   → 200 kill_switch_active
  2. NoActiveCycle      → 503 no_active_season
  3. LockBusy           → 503 lock_busy
  4. IllegalTransition  → 409 illegal_transition
  5. TimeFenceViolation → 409 time_fence_violation
  6. already_applied    → 200 already_applied (from executor result, not exception)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.domain.cycle_executor import (
    IllegalTransition,
    KillSwitchActive,
    LockBusy,
    NoActiveCycle,
    TimeFenceViolation,
    TransitionResult,
)
from app.domain.watchdog import WatchdogResult
from app.main import create_app
from app.middleware.hmac_tick import TICK_SIGNATURE_HEADER
from app.settings import get_settings

_TICK_SECRET = "test-tick-secret"
_URL = "/api/v1/internal/transition"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sign(body: bytes) -> str:
    return base64.b64encode(
        hmac.new(_TICK_SECRET.encode(), body, hashlib.sha256).digest()
    ).decode("ascii")


def _body(**kwargs: Any) -> bytes:
    payload: dict[str, Any] = {
        "to": kwargs.pop("to", "ESTRENO"),
        "ts": kwargs.pop("ts", int(time.time())),
        "trigger_id": kwargs.pop("trigger_id", str(uuid.uuid4())),
        **kwargs,
    }
    return json.dumps(payload).encode()


def _post(client: TestClient, **kwargs: Any) -> Any:
    raw = _body(**kwargs)
    return client.post(
        _URL,
        content=raw,
        headers={
            "Content-Type": "application/json",
            TICK_SIGNATURE_HEADER: _sign(raw),
        },
    )


async def _mock_session() -> AsyncSession:  # type: ignore[misc]
    yield AsyncMock(spec=AsyncSession)


_NOW = datetime(2026, 6, 9, 15, 0, 0, tzinfo=UTC)
_APPLIED_RESULT = TransitionResult(
    status="applied",
    transition_id=42,
    applied_at=_NOW,
    side_effect_name=None,
    cycle_id=1,
    chapter_id=1,
)
_ALREADY_RESULT = TransitionResult(
    status="already_applied",
    transition_id=None,
    applied_at=_NOW,
    side_effect_name=None,
    cycle_id=1,
    chapter_id=1,
)
_SIDE_EFFECT_RESULT = TransitionResult(
    status="applied",
    transition_id=43,
    applied_at=_NOW,
    side_effect_name="director_filter",
    cycle_id=1,
    chapter_id=5,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("TICK_SECRET", _TICK_SECRET)
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://mock/mock")
    get_settings.cache_clear()
    a = create_app()
    a.dependency_overrides[get_session] = _mock_session
    try:
        yield a
    finally:
        get_settings.cache_clear()


@pytest.fixture
def client(test_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(test_app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Failure mode 1 — KillSwitchActive → 200 kill_switch_active
# ---------------------------------------------------------------------------


def test_kill_switch_active_returns_200(client: TestClient) -> None:
    with patch(
        "app.api.internal_transition.executor_transition",
        new=AsyncMock(side_effect=KillSwitchActive(reason="manual override")),
    ):
        r = _post(client, to="ESTRENO")

    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "kill_switch_active"
    assert data["reason"] == "manual override"


def test_kill_switch_active_with_none_reason(client: TestClient) -> None:
    with patch(
        "app.api.internal_transition.executor_transition",
        new=AsyncMock(side_effect=KillSwitchActive(reason=None)),
    ):
        r = _post(client, to="ESTRENO")

    assert r.status_code == 200
    assert r.json()["status"] == "kill_switch_active"
    assert r.json()["reason"] is None


# ---------------------------------------------------------------------------
# Failure mode 2 — NoActiveCycle → 503 no_active_season
# ---------------------------------------------------------------------------


def test_no_active_cycle_returns_503(client: TestClient) -> None:
    with patch(
        "app.api.internal_transition.executor_transition",
        new=AsyncMock(side_effect=NoActiveCycle("no cycle")),
    ):
        r = _post(client, to="ESTRENO")

    assert r.status_code == 503
    data = r.json()
    assert data["code"] == "no_active_season"
    assert data["status"] == 503


# ---------------------------------------------------------------------------
# Failure mode 3 — LockBusy → 503 lock_busy
# ---------------------------------------------------------------------------


def test_lock_busy_returns_503(client: TestClient) -> None:
    with patch(
        "app.api.internal_transition.executor_transition",
        new=AsyncMock(side_effect=LockBusy(cycle_id=7)),
    ):
        r = _post(client, to="ESTRENO")

    assert r.status_code == 503
    data = r.json()
    assert data["code"] == "lock_busy"
    assert "7" in data["detail"]


# ---------------------------------------------------------------------------
# Failure mode 4 — IllegalTransition → 409 illegal_transition
# ---------------------------------------------------------------------------


def test_illegal_transition_returns_409(client: TestClient) -> None:
    with patch(
        "app.api.internal_transition.executor_transition",
        new=AsyncMock(
            side_effect=IllegalTransition("PENDING_RELEASE", "GENERACION")
        ),
    ):
        r = _post(client, to="GENERACION")

    assert r.status_code == 409
    data = r.json()
    assert data["code"] == "illegal_transition"
    assert "PENDING_RELEASE" in data["detail"]
    assert "GENERACION" in data["detail"]


# ---------------------------------------------------------------------------
# Failure mode 5 — TimeFenceViolation → 409 time_fence_violation
# ---------------------------------------------------------------------------


def test_time_fence_violation_returns_409(client: TestClient) -> None:
    earliest = _NOW + timedelta(seconds=55)
    with patch(
        "app.api.internal_transition.executor_transition",
        new=AsyncMock(
            side_effect=TimeFenceViolation(
                from_state="ESTRENO",
                to_state="RECEPCION_IDEAS",
                elapsed_s=5.0,
                min_dwell_s=60,
                earliest_at=earliest,
            )
        ),
    ):
        r = _post(client, to="RECEPCION_IDEAS")

    assert r.status_code == 409
    data = r.json()
    assert data["code"] == "time_fence_violation"
    assert "60" in data["detail"]
    assert "ESTRENO" in data["detail"]


# ---------------------------------------------------------------------------
# Failure mode 6 — already_applied → 200 already_applied
# ---------------------------------------------------------------------------


def test_already_applied_returns_200(client: TestClient) -> None:
    with patch(
        "app.api.internal_transition.executor_transition",
        new=AsyncMock(return_value=_ALREADY_RESULT),
    ):
        r = _post(client, to="ESTRENO")

    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "already_applied"
    assert "applied_at" in data


# ---------------------------------------------------------------------------
# Happy path — applied (no side effect)
# ---------------------------------------------------------------------------


def test_applied_returns_202(client: TestClient) -> None:
    with patch(
        "app.api.internal_transition.executor_transition",
        new=AsyncMock(return_value=_APPLIED_RESULT),
    ):
        r = _post(client, to="ESTRENO")

    assert r.status_code == 202
    data = r.json()
    assert data["status"] == "applied"
    assert data["transition_id"] == 42
    assert data["side_effect_spawned"] is None


# ---------------------------------------------------------------------------
# Happy path — applied with side effect spawned
# ---------------------------------------------------------------------------


def test_applied_with_side_effect_returns_202_and_spawns(
    client: TestClient,
) -> None:
    with (
        patch(
            "app.api.internal_transition.executor_transition",
            new=AsyncMock(return_value=_SIDE_EFFECT_RESULT),
        ),
        patch("app.api.internal_transition.run_safe", new=AsyncMock()) as mock_run,
    ):
        r = _post(client, to="FILTERING")

    assert r.status_code == 202
    data = r.json()
    assert data["side_effect_spawned"] == "director_filter"
    # BackgroundTask was added (TestClient executes them synchronously).
    mock_run.assert_awaited_once()
    kw = mock_run.call_args.kwargs
    assert kw["name"] == "director_filter"
    assert kw["chapter_id"] == 5
    assert kw["cycle_id"] == 1


# ---------------------------------------------------------------------------
# Watchdog dispatch
# ---------------------------------------------------------------------------


def test_watchdog_dispatch_returns_verdict(client: TestClient) -> None:
    fake_result = WatchdogResult(
        verdict="stuck_filtering",
        cycle_id=3,
        cycle_state="FILTERING",
        elapsed_seconds=21600.0,
        forced_failed=True,
        discord_posted=False,
    )
    with patch(
        "app.api.internal_transition.watchdog_check",
        new=AsyncMock(return_value=fake_result),
    ):
        r = _post(client, to="WATCHDOG")

    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "watchdog_ok"
    assert data["verdict"] == "stuck_filtering"
    assert data["forced_failed"] is True


# ---------------------------------------------------------------------------
# X-Dev-Skip-Dwell header (non-prod only)
# ---------------------------------------------------------------------------


def test_skip_dwell_header_honoured_in_test_env(
    test_app: FastAPI, client: TestClient
) -> None:
    """X-Dev-Skip-Dwell: 1 passes skip_dwell=True to the executor in test env."""
    captured: list[dict[str, Any]] = []

    async def mock_exec(session, requested_to, triggered_by, trigger_id, **kw):  # type: ignore[no-untyped-def]
        captured.append(kw)
        return _APPLIED_RESULT

    with patch("app.api.internal_transition.executor_transition", new=mock_exec):
        raw = _body(to="ESTRENO")
        client.post(
            _URL,
            content=raw,
            headers={
                "Content-Type": "application/json",
                TICK_SIGNATURE_HEADER: _sign(raw),
                "X-Dev-Skip-Dwell": "1",
            },
        )

    assert captured[0].get("skip_dwell") is True


def test_skip_dwell_ignored_in_prod_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """X-Dev-Skip-Dwell: 1 is ignored when ENV=prod."""
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("TICK_SECRET", _TICK_SECRET)
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://mock/mock")
    get_settings.cache_clear()

    prod_app = create_app()
    prod_app.dependency_overrides[get_session] = _mock_session
    captured: list[dict[str, Any]] = []

    async def mock_exec(session, requested_to, triggered_by, trigger_id, **kw):  # type: ignore[no-untyped-def]
        captured.append(kw)
        return _APPLIED_RESULT

    with (
        patch("app.api.internal_transition.executor_transition", new=mock_exec),
        TestClient(prod_app, raise_server_exceptions=False) as prod_client,
    ):
        raw = _body(to="ESTRENO")
        prod_client.post(
            _URL,
            content=raw,
            headers={
                "Content-Type": "application/json",
                TICK_SIGNATURE_HEADER: _sign(raw),
                "X-Dev-Skip-Dwell": "1",
            },
        )

    get_settings.cache_clear()
    assert captured[0].get("skip_dwell") is False
