"""Integration tests: POST /api/v1/auth/redeem-invite.

Module 002 / Task T-015.

Uses httpx.AsyncClient with ASGITransport against the real app + real DB.
Each test creates its own invite and cleans up afterwards.

All tests skip when DATABASE_URL is the conftest placeholder.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import sqlalchemy as sa

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.main import create_app
from tests.fixtures import require_real_db_url


def _app() -> FastAPI:
    return create_app()


async def _create_invite(note: str) -> str:
    """Insert a fresh unused invite; returns the code string."""
    url = require_real_db_url()
    engine = create_async_engine(url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    code = str(__import__("app.domain.invites", fromlist=["InviteCode"]).InviteCode.generate())
    expires_at = datetime.now(UTC) + timedelta(days=7)
    async with factory() as s:
        await s.execute(
            sa.text(
                "INSERT INTO invites (code, issued_by, expires_at, status, note) "
                "VALUES (:code, 'test', :exp, 'unused', :note)"
            ),
            {"code": code, "exp": expires_at, "note": note},
        )
        await s.commit()
    await engine.dispose()
    return code


async def _cleanup(code: str) -> None:
    """Delete the user (if any) and the invite for *code*."""
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


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_redeem_returns_201_with_jwt(db_session: AsyncSession) -> None:
    note = f"test-redeem-{uuid4().hex[:8]}"
    code = await _create_invite(note)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app()),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/v1/auth/redeem-invite",
                json={"invite_code": code, "display_name": "NuevoJugador"},
            )

        assert resp.status_code == 201
        body = resp.json()
        assert "jwt" in body
        assert "device_secret" in body
        assert body["user"]["display_name"] == "NuevoJugador"
        assert len(body["device_secret"]) == 43  # base64url of 32 bytes
        assert "jwt_expires_at" in body
    finally:
        await _cleanup(code)


async def test_redeem_normalises_display_name(db_session: AsyncSession) -> None:
    note = f"test-redeem-norm-{uuid4().hex[:8]}"
    code = await _create_invite(note)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app()),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/v1/auth/redeem-invite",
                # Leading/trailing spaces should be trimmed
                json={"invite_code": code, "display_name": "  Lucía  "},
            )
        assert resp.status_code == 201
        assert resp.json()["user"]["display_name"] == "Lucía"
    finally:
        await _cleanup(code)


# ---------------------------------------------------------------------------
# 404 — invite not redeemable
# ---------------------------------------------------------------------------


async def test_redeem_nonexistent_invite_returns_404(db_session: AsyncSession) -> None:
    from app.domain.invites import InviteCode

    code = str(InviteCode.generate())  # never inserted
    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/auth/redeem-invite",
            json={"invite_code": code, "display_name": "AlguienX"},
        )
    assert resp.status_code == 404
    assert resp.json()["code"] == "invite_not_redeemable"


# ---------------------------------------------------------------------------
# 422 — schema validation
# ---------------------------------------------------------------------------


async def test_redeem_invalid_invite_format_returns_422(db_session: AsyncSession) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/auth/redeem-invite",
            json={"invite_code": "NOT-VALID!", "display_name": "Alguien"},
        )
    assert resp.status_code == 422


async def test_redeem_missing_fields_returns_422(db_session: AsyncSession) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test"
    ) as client:
        resp = await client.post("/api/v1/auth/redeem-invite", json={})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 429 — rate limit
# ---------------------------------------------------------------------------


async def test_redeem_rate_limit_returns_429(db_session: AsyncSession) -> None:
    """Exhaust the 5-per-hour limit from a single IP and verify 429."""
    from app.domain.invites import InviteCode

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app()),
            base_url="http://test",
            headers={"X-Forwarded-For": "10.0.0.99"},
        ) as client:
            # Burn 5 attempts (each with a fresh code so 404 doesn't short-circuit)
            for _i in range(5):
                fake_code = str(InviteCode.generate())
                await client.post(
                    "/api/v1/auth/redeem-invite",
                    json={"invite_code": fake_code, "display_name": "Tester"},
                )

            # 6th attempt should be rate-limited
            resp = await client.post(
                "/api/v1/auth/redeem-invite",
                json={"invite_code": str(InviteCode.generate()), "display_name": "Tester"},
            )

        assert resp.status_code == 429
        assert resp.json()["code"] == "rate_limited"
        assert "Retry-After" in resp.headers
    finally:
        # Clean up rate-limit bucket (cosmetic — test DB resets)
        url = require_real_db_url()
        engine = create_async_engine(url)
        factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            engine, expire_on_commit=False
        )
        async with factory() as s:
            await s.execute(
                sa.text(
                    "DELETE FROM rate_limit_buckets WHERE bucket_key = 'redeem:ip:10.0.0.99'"
                )
            )
            await s.commit()
        await engine.dispose()
