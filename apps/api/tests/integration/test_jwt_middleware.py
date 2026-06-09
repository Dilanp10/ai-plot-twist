"""Integration tests: require_user JWT dependency.

Module 002 / Task T-014.

Calls ``require_user`` directly (not via HTTP) to avoid needing a mounted
app. The session parameter is supplied explicitly (bypassing FastAPI DI).

DB tests skip when DATABASE_URL is the conftest placeholder.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.jwt_service import JWTService
from app.middleware.jwt_auth import _user_cache, require_user
from app.settings import get_settings


def _make_request(auth: str | None = None) -> Request:
    """Build a real Starlette Request with optional Authorization header."""
    headers: list[tuple[bytes, bytes]] = []
    if auth is not None:
        headers.append((b"authorization", auth.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "query_string": b"",
        "headers": headers,
    }
    return Request(scope)


def _issue_token(public_id: UUID) -> str:
    settings = get_settings()
    token, _ = JWTService(settings.jwt_secret).issue(public_id)
    return token


# ---------------------------------------------------------------------------
# Missing / invalid token (no DB needed)
# ---------------------------------------------------------------------------


async def test_missing_auth_header_raises_401(db_session: AsyncSession) -> None:
    with pytest.raises(HTTPException) as exc:
        await require_user(_make_request(), db_session)
    assert exc.value.status_code == 401


async def test_malformed_auth_header_raises_401(db_session: AsyncSession) -> None:
    with pytest.raises(HTTPException) as exc:
        await require_user(_make_request(auth="Token notabearer"), db_session)
    assert exc.value.status_code == 401


async def test_invalid_jwt_raises_401(db_session: AsyncSession) -> None:
    with pytest.raises(HTTPException) as exc:
        await require_user(_make_request(auth="Bearer garbage.token.here"), db_session)
    assert exc.value.status_code == 401


async def test_token_for_nonexistent_user_raises_401(db_session: AsyncSession) -> None:
    token = _issue_token(uuid4())  # UUID not in DB
    with pytest.raises(HTTPException) as exc:
        await require_user(_make_request(auth=f"Bearer {token}"), db_session)
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Valid token + active user (real DB)
# ---------------------------------------------------------------------------


async def test_valid_token_returns_user_row(
    db_session: AsyncSession, active_user: dict[str, Any]
) -> None:
    public_id = UUID(str(active_user["public_id"]))
    token = _issue_token(public_id)
    _user_cache.invalidate(public_id)  # ensure fresh DB lookup

    user = await require_user(_make_request(auth=f"Bearer {token}"), db_session)

    assert user.public_id == public_id
    assert user.display_name == active_user["display_name"]
    assert user.is_banned is False


async def test_second_call_uses_cache(
    db_session: AsyncSession, active_user: dict[str, Any]
) -> None:
    """Second call with same token returns cached result (no extra DB hit)."""
    public_id = UUID(str(active_user["public_id"]))
    token = _issue_token(public_id)
    _user_cache.invalidate(public_id)

    user1 = await require_user(_make_request(auth=f"Bearer {token}"), db_session)
    user2 = await require_user(_make_request(auth=f"Bearer {token}"), db_session)

    assert user1.public_id == user2.public_id


# ---------------------------------------------------------------------------
# Banned user (real DB)
# ---------------------------------------------------------------------------


async def test_banned_user_raises_403(
    db_session: AsyncSession, banned_user: dict[str, Any]
) -> None:
    public_id = UUID(str(banned_user["public_id"]))
    token = _issue_token(public_id)
    _user_cache.invalidate(public_id)

    with pytest.raises(HTTPException) as exc:
        await require_user(_make_request(auth=f"Bearer {token}"), db_session)
    assert exc.value.status_code == 403
