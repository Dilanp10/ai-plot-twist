"""Integration tests: POST /auth/refresh + GET /auth/me.

Module 002 / Tasks T-016 + T-017.

Uses httpx.AsyncClient with ASGITransport. All tests skip without a real DB.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.jwt_service import JWTService
from app.main import create_app
from app.settings import get_settings
from tests.fixtures import require_real_db_url

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _app() -> FastAPI:
    return create_app()


async def _create_invite() -> str:
    url = require_real_db_url()
    engine = create_async_engine(url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    from app.domain.invites import InviteCode

    code = str(InviteCode.generate())
    expires_at = datetime.now(UTC) + timedelta(days=7)
    async with factory() as s:
        await s.execute(
            sa.text(
                "INSERT INTO invites (code, issued_by, expires_at, status) "
                "VALUES (:code, 'test', :exp, 'unused')"
            ),
            {"code": code, "exp": expires_at},
        )
        await s.commit()
    await engine.dispose()
    return code


async def _cleanup(code: str) -> None:
    url = require_real_db_url()
    engine = create_async_engine(url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    async with factory() as s:
        await s.execute(
            sa.text("DELETE FROM users WHERE invite_code = :c"), {"c": code}
        )
        await s.execute(
            sa.text("DELETE FROM invites WHERE code = :c"), {"c": code}
        )
        await s.commit()
    await engine.dispose()


async def _redeem(code: str, name: str = "Jugador") -> dict[str, Any]:
    """Redeem invite via the real endpoint; return the response body."""
    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/auth/redeem-invite",
            json={"invite_code": code, "display_name": name},
        )
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


# ---------------------------------------------------------------------------
# T-016 — POST /api/v1/auth/refresh
# ---------------------------------------------------------------------------


async def test_refresh_returns_new_jwt(db_session: AsyncSession) -> None:
    code = await _create_invite()
    try:
        body = await _redeem(code)
        device_secret = body["device_secret"]
        original_jwt = body["jwt"]

        async with AsyncClient(
            transport=ASGITransport(app=_app()), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/auth/refresh",
                json={"device_secret": device_secret},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "jwt" in data
        assert "jwt_expires_at" in data
        # New token must be a valid JWT and differ from the original
        assert data["jwt"] != original_jwt
    finally:
        await _cleanup(code)


async def test_refresh_new_jwt_is_verifiable(db_session: AsyncSession) -> None:
    """The new JWT from /refresh must decode correctly."""
    code = await _create_invite()
    try:
        body = await _redeem(code)
        device_secret = body["device_secret"]

        async with AsyncClient(
            transport=ASGITransport(app=_app()), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/auth/refresh",
                json={"device_secret": device_secret},
            )

        new_jwt = resp.json()["jwt"]
        settings = get_settings()
        claims = JWTService(settings.jwt_secret).verify(new_jwt)
        assert claims is not None
        # sub should be the same user
        assert claims.sub == UUID(body["user"]["public_id"])
    finally:
        await _cleanup(code)


async def test_refresh_invalid_secret_returns_401(db_session: AsyncSession) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/auth/refresh",
            json={"device_secret": "not-a-valid-secret"},
        )
    assert resp.status_code == 401
    assert resp.json()["code"] == "device_secret_invalid"


async def test_refresh_missing_field_returns_422(db_session: AsyncSession) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        resp = await client.post("/api/v1/auth/refresh", json={})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# T-017 — GET /api/v1/auth/me
# ---------------------------------------------------------------------------


async def test_me_returns_user_data(
    db_session: AsyncSession, active_user: dict[str, Any]
) -> None:
    public_id = UUID(str(active_user["public_id"]))
    settings = get_settings()
    token, _ = JWTService(settings.jwt_secret).issue(public_id)

    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["public_id"] == str(public_id)
    assert data["user"]["display_name"] == active_user["display_name"]


async def test_me_updates_last_seen_at(
    db_session: AsyncSession, active_user: dict[str, Any]
) -> None:
    public_id = UUID(str(active_user["public_id"]))
    settings = get_settings()
    token, _ = JWTService(settings.jwt_secret).issue(public_id)

    before = datetime.now(UTC)

    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

    # Verify DB was updated via db_session
    result = await db_session.execute(
        sa.text("SELECT last_seen_at FROM users WHERE public_id = :pid"),
        {"pid": public_id},
    )
    last_seen = result.scalar_one()
    last_seen_utc = last_seen.replace(tzinfo=UTC)
    assert last_seen_utc >= before, "last_seen_at should be updated after /me"


async def test_me_missing_jwt_returns_401(db_session: AsyncSession) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


async def test_me_banned_user_returns_403(
    db_session: AsyncSession, banned_user: dict[str, Any]
) -> None:
    public_id = UUID(str(banned_user["public_id"]))
    settings = get_settings()
    token, _ = JWTService(settings.jwt_secret).issue(public_id)

    # Invalidate cache to force DB lookup
    from app.middleware.jwt_auth import _user_cache
    _user_cache.invalidate(public_id)

    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403
