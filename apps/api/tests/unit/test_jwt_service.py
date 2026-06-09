"""Unit tests: JWTService.

Module 002 / Task T-005.

Coverage:
  - issue + verify roundtrip returns correct JWTClaims
  - tampered signature → None
  - expired token (outside leeway) → None
  - 60 s leeway: token expired 30 s ago → still valid
  - wrong audience → None
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt as _jwt
import pytest

from app.domain.jwt_service import JWT_ALGORITHM, JWT_AUDIENCE, JWT_TTL_DAYS, JWTService

_SECRET = "super-secret-test-key-that-is-at-least-32-bytes"


@pytest.fixture
def svc() -> JWTService:
    return JWTService(_SECRET)


# ---------------------------------------------------------------------------
# Issue + verify roundtrip
# ---------------------------------------------------------------------------


def test_roundtrip_returns_claims(svc: JWTService) -> None:
    uid = uuid4()
    token, _exp = svc.issue(uid)
    claims = svc.verify(token)

    assert claims is not None
    assert claims.sub == uid
    # exp should be ~90 days from now; allow ±5 s for test execution time
    expected = datetime.now(UTC) + timedelta(days=JWT_TTL_DAYS)
    assert abs((claims.exp - expected).total_seconds()) < 5


def test_issue_returns_future_expiry(svc: JWTService) -> None:
    _, exp = svc.issue(uuid4())
    assert exp > datetime.now(UTC)


# ---------------------------------------------------------------------------
# Tampered signature
# ---------------------------------------------------------------------------


def test_tampered_signature_returns_none(svc: JWTService) -> None:
    token, _ = svc.issue(uuid4())
    parts = token.split(".")
    # Change the FIRST character of the signature (never a padding-only char,
    # so the decoded bytes always differ → HMAC check always fails).
    first_char = parts[2][0]
    parts[2] = ("A" if first_char != "A" else "B") + parts[2][1:]
    tampered = ".".join(parts)
    assert svc.verify(tampered) is None


# ---------------------------------------------------------------------------
# Expired token
# ---------------------------------------------------------------------------


def test_expired_token_returns_none(svc: JWTService) -> None:
    """Token that expired 2 days ago — well outside the 60 s leeway."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(uuid4()),
        "aud": JWT_AUDIENCE,
        "iat": now - timedelta(days=2),
        "exp": now - timedelta(days=1),
    }
    token = _jwt.encode(payload, _SECRET, algorithm=JWT_ALGORITHM)
    assert svc.verify(token) is None


# ---------------------------------------------------------------------------
# 60 s leeway
# ---------------------------------------------------------------------------


def test_leeway_accepts_recently_expired_token(svc: JWTService) -> None:
    """Token expired 30 s ago — within the 60 s leeway → still valid."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(uuid4()),
        "aud": JWT_AUDIENCE,
        "iat": now - timedelta(seconds=120),
        "exp": now - timedelta(seconds=30),
    }
    token = _jwt.encode(payload, _SECRET, algorithm=JWT_ALGORITHM)
    claims = svc.verify(token)
    assert claims is not None


def test_leeway_rejects_token_beyond_leeway(svc: JWTService) -> None:
    """Token expired 90 s ago — outside the 60 s leeway → None."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(uuid4()),
        "aud": JWT_AUDIENCE,
        "iat": now - timedelta(seconds=200),
        "exp": now - timedelta(seconds=90),
    }
    token = _jwt.encode(payload, _SECRET, algorithm=JWT_ALGORITHM)
    assert svc.verify(token) is None


# ---------------------------------------------------------------------------
# Wrong audience
# ---------------------------------------------------------------------------


def test_wrong_audience_returns_none(svc: JWTService) -> None:
    now = datetime.now(UTC)
    payload = {
        "sub": str(uuid4()),
        "aud": "wrong-audience",
        "iat": now,
        "exp": now + timedelta(days=1),
    }
    token = _jwt.encode(payload, _SECRET, algorithm=JWT_ALGORITHM)
    assert svc.verify(token) is None


def test_missing_audience_returns_none(svc: JWTService) -> None:
    now = datetime.now(UTC)
    payload = {
        "sub": str(uuid4()),
        "iat": now,
        "exp": now + timedelta(days=1),
    }
    token = _jwt.encode(payload, _SECRET, algorithm=JWT_ALGORITHM)
    assert svc.verify(token) is None


# ---------------------------------------------------------------------------
# Wrong secret
# ---------------------------------------------------------------------------


def test_wrong_secret_returns_none(svc: JWTService) -> None:
    other = JWTService("different-secret-key-also-at-least-32-bytes")
    token, _ = other.issue(uuid4())
    assert svc.verify(token) is None


# ---------------------------------------------------------------------------
# Garbage input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_token", ["", "not.a.jwt", "aaa", "x.y.z"])
def test_garbage_input_returns_none(svc: JWTService, bad_token: str) -> None:
    assert svc.verify(bad_token) is None
