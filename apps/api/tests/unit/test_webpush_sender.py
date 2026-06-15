"""Unit tests: WebPushSender (T-004).

All paths are exercised by patching :func:`pywebpush.webpush` at the
``app.infra.webpush_sender`` import site so no network I/O happens.
Coverage:
  1. 201 / 204 → SendResult.SUCCESS, status carried.
  2. 410 (Gone) raised by pywebpush → SendResult.GONE.
  3. 404 raised by pywebpush → SendResult.GONE.
  4. 5xx raised by pywebpush → SendResult.FAILED, status carried.
  5. Unknown WebPushException (no response) → FAILED.
  6. Connection error / timeout → FAILED.
  7. Bytes payload pass-through.
  8. Dict payload JSON-encoded.
  9. VAPID claims include the configured ``sub``.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from pywebpush import WebPushException

from app.infra.push_subscriptions_repo import Subscription
from app.infra.webpush_sender import (
    SendOutcome,
    SendResult,
    WebPushSender,
)


def _sub(subscription_id: int = 1) -> Subscription:
    return Subscription(
        id=subscription_id,
        user_id=42,
        endpoint="https://push.example/abc",
        p256dh_key="pk-base64",
        auth_key="ak-base64",
    )


def _sender() -> WebPushSender:
    return WebPushSender(
        vapid_private_key="-----BEGIN PRIVATE KEY-----\nFAKE\n-----END PRIVATE KEY-----",
        vapid_subject="mailto:ops@example.com",
    )


def _response(status: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    return resp


_TARGET = "app.infra.webpush_sender.webpush"


# ---------------------------------------------------------------------------
# Success paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [201, 204])
async def test_success_status_returns_success(status: int) -> None:
    with patch(_TARGET, return_value=_response(status)):
        outcome = await _sender().send(_sub(), {"x": 1})
    assert outcome == SendOutcome(
        subscription_id=1, result=SendResult.SUCCESS, status_code=status
    )


# ---------------------------------------------------------------------------
# Gone paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 410])
async def test_gone_status_raised_by_pywebpush_returns_gone(
    status: int,
) -> None:
    exc = WebPushException("gone", response=_response(status))
    with patch(_TARGET, side_effect=exc):
        outcome = await _sender().send(_sub(7), {"x": 1})
    assert outcome.subscription_id == 7
    assert outcome.result == SendResult.GONE
    assert outcome.status_code == status


@pytest.mark.asyncio
async def test_gone_status_in_response_body_returns_gone() -> None:
    """Some push services return the status in the response without raising."""
    with patch(_TARGET, return_value=_response(410)):
        outcome = await _sender().send(_sub(8), {"x": 1})
    assert outcome.result == SendResult.GONE
    assert outcome.status_code == 410


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 429, 500, 502, 503])
async def test_non_terminal_status_returns_failed(status: int) -> None:
    exc = WebPushException("oops", response=_response(status))
    with patch(_TARGET, side_effect=exc):
        outcome = await _sender().send(_sub(), {"x": 1})
    assert outcome.result == SendResult.FAILED
    assert outcome.status_code == status


@pytest.mark.asyncio
async def test_webpush_exception_without_response_returns_failed() -> None:
    exc = WebPushException("no response attached")
    # WebPushException doesn't set .response when constructed without one;
    # the sender must still bucket as FAILED.
    exc.response = None
    with patch(_TARGET, side_effect=exc):
        outcome = await _sender().send(_sub(), {"x": 1})
    assert outcome.result == SendResult.FAILED
    assert outcome.status_code is None


@pytest.mark.asyncio
async def test_connection_error_returns_failed() -> None:
    with patch(_TARGET, side_effect=ConnectionError("timed out")):
        outcome = await _sender().send(_sub(), {"x": 1})
    assert outcome.result == SendResult.FAILED
    assert outcome.error is not None and "timed out" in outcome.error


# ---------------------------------------------------------------------------
# Payload + VAPID plumbing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bytes_payload_passed_through_verbatim() -> None:
    raw = b"\x01\x02preencoded"
    with patch(_TARGET, return_value=_response(201)) as call:
        await _sender().send(_sub(), raw)
    assert call.call_args.kwargs["data"] == raw


@pytest.mark.asyncio
async def test_dict_payload_is_json_serialised() -> None:
    payload = {"title": "x", "body": "y"}
    with patch(_TARGET, return_value=_response(201)) as call:
        await _sender().send(_sub(), payload)
    assert call.call_args.kwargs["data"] == json.dumps(payload).encode("utf-8")


@pytest.mark.asyncio
async def test_vapid_claims_carry_subject() -> None:
    with patch(_TARGET, return_value=_response(201)) as call:
        await _sender().send(_sub(), {"x": 1})
    assert call.call_args.kwargs["vapid_claims"] == {
        "sub": "mailto:ops@example.com"
    }


@pytest.mark.asyncio
async def test_subscription_info_is_assembled_correctly() -> None:
    with patch(_TARGET, return_value=_response(201)) as call:
        await _sender().send(_sub(), {"x": 1})
    assert call.call_args.kwargs["subscription_info"] == {
        "endpoint": "https://push.example/abc",
        "keys": {"p256dh": "pk-base64", "auth": "ak-base64"},
    }


@pytest.mark.asyncio
async def test_explicit_timeout_forwarded_to_pywebpush() -> None:
    with patch(_TARGET, return_value=_response(201)) as call:
        await _sender().send(_sub(), {"x": 1}, timeout=2.5)
    assert call.call_args.kwargs["timeout"] == 2.5
