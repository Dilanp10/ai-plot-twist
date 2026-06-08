"""Tests for ``POST /api/v1/internal/transition`` (module 001 stub).

Module 001 / Task T-012.

Coverage:
  - Valid HMAC + valid payload → 202 ``{accepted, noop:true}``.
  - Invalid ``to`` (not in enum) → 422 ``bad_payload``.
  - Missing ``trigger_id`` → 422 ``bad_payload``.
  - ``trigger_id`` too short (< 4) → 422 ``bad_payload``.
  - ``trigger_id`` too long (> 128) → 422 ``bad_payload``.
  - Replayed ``trigger_id`` → 409 ``trigger_replayed``.
  - FIFO eviction: filling the cache past ``max_entries`` evicts oldest;
    the evicted id can be re-submitted without triggering a replay.
  - End-to-end bad HMAC → 401 from the dependency (sanity check that
    the dep is wired up correctly).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from app.api.internal_transition import TriggerIdReplayCache, get_trigger_cache
from app.main import create_app
from app.middleware.hmac_tick import TICK_SIGNATURE_HEADER
from app.settings import get_settings

_TICK_SECRET = "test-tick-secret"
_TRANSITION_URL = "/api/v1/internal/transition"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sign(body: bytes, secret: str = _TICK_SECRET) -> str:
    """Compute the base64 HMAC-SHA256 signature for *body*."""
    return base64.b64encode(hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()).decode(
        "ascii"
    )


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
    # TestClient.post is typed as Any in starlette stubs; the runtime return
    # is httpx.Response.
    return client.post(  # type: ignore[no-any-return]
        _TRANSITION_URL,
        content=body,
        headers={
            "Content-Type": "application/json",
            TICK_SIGNATURE_HEADER: sig,
        },
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_cache() -> TriggerIdReplayCache:
    """A new replay cache per test — no state leakage between tests."""
    return TriggerIdReplayCache()


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, fresh_cache: TriggerIdReplayCache) -> Iterator[FastAPI]:
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("TICK_SECRET", _TICK_SECRET)
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    get_settings.cache_clear()

    a = create_app()
    a.dependency_overrides[get_trigger_cache] = lambda: fresh_cache
    try:
        yield a
    finally:
        get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_valid_tick_returns_202_accepted_noop(client: TestClient) -> None:
    body = _make_body(to="ESTRENO")
    response = _post(client, body)
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "noop": True}


@pytest.mark.parametrize("to_value", ["ESTRENO", "FILTERING", "GENERACION", "WATCHDOG"])
def test_all_four_enum_values_accepted(client: TestClient, to_value: str) -> None:
    """All four documented `to` values are accepted by the stub."""
    body = _make_body(to=to_value)
    response = _post(client, body)
    assert response.status_code == 202


def test_invalid_to_returns_422(client: TestClient) -> None:
    body = _make_body(to="WRONG_STATE")
    response = _post(client, body)
    assert response.status_code == 422
    body_json = response.json()
    assert body_json["code"] == "bad_payload"
    assert "to" in body_json["detail"]


def test_missing_trigger_id_returns_422(client: TestClient) -> None:
    # Manually craft a body with no trigger_id; HMAC dep validates ts only.
    body = json.dumps({"to": "WATCHDOG", "ts": int(time.time())}).encode("utf-8")
    response = _post(client, body)
    assert response.status_code == 422
    body_json = response.json()
    assert body_json["code"] == "bad_payload"
    assert "trigger_id" in body_json["detail"]


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


def test_replayed_trigger_id_returns_409(client: TestClient) -> None:
    body = _make_body(trigger_id="replay-test-12345")

    r1 = _post(client, body)
    assert r1.status_code == 202

    # Identical body → identical signature → same trigger_id reaches the cache.
    r2 = _post(client, body)
    assert r2.status_code == 409
    body_json = r2.json()
    assert body_json["code"] == "trigger_replayed"
    assert body_json["status"] == 409


def test_cache_evicts_oldest_when_full(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the cache is full, the oldest trigger_id is FIFO-evicted."""
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("TICK_SECRET", _TICK_SECRET)
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    get_settings.cache_clear()

    small_cache = TriggerIdReplayCache(max_entries=2)
    small_app = create_app()
    small_app.dependency_overrides[get_trigger_cache] = lambda: small_cache

    try:
        with TestClient(small_app) as small_client:
            # Fill the cache with [trigger-0, trigger-1].
            for idx in range(2):
                r = _post(small_client, _make_body(trigger_id=f"trigger-{idx}"))
                assert r.status_code == 202

            # Push trigger-2 → cache becomes [trigger-1, trigger-2]
            # (trigger-0 evicted).
            r = _post(small_client, _make_body(trigger_id="trigger-2"))
            assert r.status_code == 202

            # trigger-0 was evicted → re-submitting must succeed.
            r = _post(small_client, _make_body(trigger_id="trigger-0"))
            assert r.status_code == 202

            # trigger-2 is still in cache (cache is now [trigger-2, trigger-0])
            # → replay must be detected.
            r = _post(small_client, _make_body(trigger_id="trigger-2"))
            assert r.status_code == 409
    finally:
        get_settings.cache_clear()


def test_bad_hmac_returns_401_end_to_end(client: TestClient) -> None:
    """Sanity: the HMAC dependency from T-011 is wired up on this route."""
    body = _make_body()
    response = _post(client, body, signature="not-a-real-signature")
    assert response.status_code == 401
    assert response.json()["code"] == "bad_hmac"
