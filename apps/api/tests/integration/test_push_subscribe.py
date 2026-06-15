"""Integration tests: POST /api/v1/push/subscribe (Module 011 T-008).

Uses a real Postgres for the repo writes and FastAPI ``dependency_overrides``
to inject the test session so endpoint commits land on the same connection
that the tracker fixture cleans up.

Skips when DATABASE_URL is the conftest placeholder.

Coverage:
  1. 201 happy path — subscription row created, id returned.
  2. 401 — no Authorization header.
  3. 422 — missing required field (endpoint).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.domain.jwt_service import JWTService
from app.main import create_app
from app.settings import get_settings
from tests.fixtures import require_real_db_url


def _fresh_invite_code() -> str:
    src = uuid4().hex.upper()
    valid = "".join(c for c in src if c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
    while len(valid) < 8:
        valid += "A"
    return f"{valid[:4]}-{valid[4:8]}"


def _app_with_session(session: AsyncSession) -> object:
    app = create_app()

    async def _override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override
    return app


async def _seed_authed_user(session: AsyncSession) -> tuple[int, str]:
    """Insert invite + user, return (user_id, jwt_token)."""
    code = _fresh_invite_code()
    token_raw = uuid4().hex * 2
    await session.execute(
        sa.text(
            "INSERT INTO invites (code, issued_by, expires_at, status, note) "
            "VALUES (:code, 'subscribe-test', :exp, 'unused', 'push subscribe')"
        ),
        {"code": code, "exp": datetime.now(UTC) + timedelta(days=7)},
    )
    result = await session.execute(
        sa.text(
            "INSERT INTO users (display_name, invite_code, device_token) "
            "VALUES (:name, :code, :token) RETURNING id, public_id"
        ),
        {"name": f"SubUser-{uuid4().hex[:6]}", "code": code, "token": token_raw},
    )
    row = result.mappings().one()
    user_id = int(row["id"])
    public_id = UUID(str(row["public_id"]))

    settings = get_settings()
    jwt, _ = JWTService(settings.jwt_secret).issue(public_id)
    return user_id, jwt


def _subscribe_body(endpoint: str | None = None) -> dict[str, str]:
    return {
        "endpoint": endpoint or f"https://fcm.googleapis.com/fcm/send/{uuid4().hex}",
        "p256dh": "BMFbtest" + "A" * 80,
        "auth": "authkeytest",
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session; rolls back on teardown (tracker handles committed rows)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    url = require_real_db_url()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


# ---------------------------------------------------------------------------
# 1. Happy path — 201
# ---------------------------------------------------------------------------


async def test_push_subscribe_creates_subscription(
    db_session: AsyncSession,
) -> None:
    user_id, jwt = await _seed_authed_user(db_session)
    endpoint = f"https://fcm.googleapis.com/fcm/send/{uuid4().hex}"

    async with AsyncClient(
        transport=ASGITransport(app=_app_with_session(db_session)),  # type: ignore[arg-type]
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/api/v1/push/subscribe",
            json=_subscribe_body(endpoint),
            headers={"Authorization": f"Bearer {jwt}"},
        )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert "id" in data
    assert isinstance(data["id"], int)

    # Verify row exists
    row = (
        await db_session.execute(
            sa.text(
                "SELECT id FROM push_subscriptions "
                "WHERE user_id = :uid AND endpoint = :ep"
            ),
            {"uid": user_id, "ep": endpoint},
        )
    ).one_or_none()
    assert row is not None

    # Cleanup (endpoint committed, rollback won't undo it)
    await db_session.execute(
        sa.text("DELETE FROM push_subscriptions WHERE user_id = :uid"),
        {"uid": user_id},
    )
    await db_session.execute(
        sa.text("DELETE FROM users WHERE id = :uid"),
        {"uid": user_id},
    )
    await db_session.commit()


# ---------------------------------------------------------------------------
# 2. 401 — no JWT
# ---------------------------------------------------------------------------


async def test_push_subscribe_returns_401_without_jwt(
    db_session: AsyncSession,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=_app_with_session(db_session)),  # type: ignore[arg-type]
        base_url="http://test",
    ) as client:
        resp = await client.post("/api/v1/push/subscribe", json=_subscribe_body())
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# 3. 422 — missing required field
# ---------------------------------------------------------------------------


async def test_push_subscribe_returns_422_on_missing_field(
    db_session: AsyncSession,
) -> None:
    _, jwt = await _seed_authed_user(db_session)
    bad_body = {"p256dh": "key", "auth": "auth"}  # missing endpoint

    async with AsyncClient(
        transport=ASGITransport(app=_app_with_session(db_session)),  # type: ignore[arg-type]
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/api/v1/push/subscribe",
            json=bad_body,
            headers={"Authorization": f"Bearer {jwt}"},
        )
    assert resp.status_code == 422, resp.text
