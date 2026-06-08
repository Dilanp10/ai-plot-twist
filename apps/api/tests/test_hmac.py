"""Tests for the HMAC tick verification dependency.

Module 001 / Task T-011.

Coverage:
  - Valid signature + fresh ts → 200 from the test route (payload echoed).
  - Bad signature → 401 ``bad_hmac``.
  - Missing signature header → 401 ``missing_signature``.
  - Drifted timestamp (> ±300 s) → 409 ``ts_drift``.
  - Missing TICK_SECRET → 503 ``tick_secret_missing``.
  - Malformed JSON body → 422 ``bad_payload``.
  - Body missing the ``ts`` field → 422 ``bad_payload``.

All error responses are validated to be RFC 7807 (Problem+JSON) per the
contract in ``specs/001-project-bootstrap/contracts/health.yaml``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.main import create_app
from app.middleware.hmac_tick import TICK_SIGNATURE_HEADER, verify_hmac_tick
from app.settings import get_settings

_TEST_TICK_SECRET = "test-tick-secret"
_TEST_ROUTE = "/__test/hmac"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sign(body: bytes, secret: str = _TEST_TICK_SECRET) -> str:
    """Compute the base64 HMAC-SHA256 signature the dependency expects."""
    return base64.b64encode(hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()).decode(
        "ascii"
    )


def _make_payload(ts: int | None = None, **extra: Any) -> bytes:
    """Build a JSON body. If *ts* is None, use current epoch."""
    payload: dict[str, Any] = {"to": "WATCHDOG", "trigger_id": "test"}
    payload["ts"] = ts if ts is not None else int(time.time())
    payload.update(extra)
    return json.dumps(payload).encode("utf-8")


def _build_hmac_app(monkeypatch: pytest.MonkeyPatch, *, tick_secret: str | None) -> FastAPI:
    """Build a FastAPI app with a test endpoint that uses the HMAC dependency.

    If *tick_secret* is ``None``, the env var is *deleted* so the dep sees
    a missing secret.
    """
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    if tick_secret is None:
        monkeypatch.delenv("TICK_SECRET", raising=False)
    else:
        monkeypatch.setenv("TICK_SECRET", tick_secret)
    get_settings.cache_clear()

    app = create_app()

    @app.post(_TEST_ROUTE)
    async def _test_route(
        payload: dict[str, Any] = Depends(verify_hmac_tick),
    ) -> dict[str, Any]:
        return {"ok": True, "payload": payload}

    return app


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    """App with TICK_SECRET configured (happy-path baseline)."""
    yield _build_hmac_app(monkeypatch, tick_secret=_TEST_TICK_SECRET)
    get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_valid_signature_and_fresh_ts_passes(client: TestClient) -> None:
    body = _make_payload()
    sig = _sign(body)
    response = client.post(
        _TEST_ROUTE,
        content=body,
        headers={
            "Content-Type": "application/json",
            TICK_SIGNATURE_HEADER: sig,
        },
    )
    assert response.status_code == 200
    out = response.json()
    assert out["ok"] is True
    assert out["payload"]["to"] == "WATCHDOG"
    assert out["payload"]["trigger_id"] == "test"


def test_bad_signature_returns_401_bad_hmac(client: TestClient) -> None:
    body = _make_payload()
    response = client.post(
        _TEST_ROUTE,
        content=body,
        headers={
            "Content-Type": "application/json",
            TICK_SIGNATURE_HEADER: "not-a-real-signature",
        },
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    body_json = response.json()
    assert body_json["code"] == "bad_hmac"
    assert body_json["status"] == 401
    assert body_json["type"] == "about:blank"


def test_missing_signature_header_returns_401(client: TestClient) -> None:
    body = _make_payload()
    response = client.post(
        _TEST_ROUTE,
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "missing_signature"


def test_drifted_timestamp_returns_409_ts_drift(client: TestClient) -> None:
    # 10 minutes in the past — well past the 300 s tolerance.
    old_ts = int(time.time()) - 600
    body = _make_payload(ts=old_ts)
    sig = _sign(body)
    response = client.post(
        _TEST_ROUTE,
        content=body,
        headers={
            "Content-Type": "application/json",
            TICK_SIGNATURE_HEADER: sig,
        },
    )
    assert response.status_code == 409
    body_json = response.json()
    assert body_json["code"] == "ts_drift"


def test_missing_tick_secret_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """When TICK_SECRET is unset on the server, /internal/* returns 503."""
    no_secret_app = _build_hmac_app(monkeypatch, tick_secret=None)
    try:
        with TestClient(no_secret_app) as no_secret_client:
            body = _make_payload()
            # Even a "valid" signature is irrelevant — server can't compute one.
            response = no_secret_client.post(
                _TEST_ROUTE,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    TICK_SIGNATURE_HEADER: "doesnt-matter",
                },
            )
        assert response.status_code == 503
        assert response.json()["code"] == "tick_secret_missing"
    finally:
        get_settings.cache_clear()


def test_malformed_json_body_returns_422(client: TestClient) -> None:
    body = b"this is not json"
    sig = _sign(body)
    response = client.post(
        _TEST_ROUTE,
        content=body,
        headers={
            "Content-Type": "application/json",
            TICK_SIGNATURE_HEADER: sig,
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "bad_payload"


def test_body_missing_ts_returns_422(client: TestClient) -> None:
    body = json.dumps({"to": "WATCHDOG", "trigger_id": "test"}).encode("utf-8")
    sig = _sign(body)
    response = client.post(
        _TEST_ROUTE,
        content=body,
        headers={
            "Content-Type": "application/json",
            TICK_SIGNATURE_HEADER: sig,
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "bad_payload"
