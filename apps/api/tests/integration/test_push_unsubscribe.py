"""Integration tests: DELETE /api/v1/push/subscriptions/{id} (Module 011 T-008).

Skips when DATABASE_URL is the conftest placeholder.

Coverage:
  1. 204 happy path — subscription deleted.
  2. 404 — subscription does not exist.
  3. 404 — subscription belongs to a different user (ownership enforced).
  4. 401 — no Authorization header.
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
from app.infra.push_subscriptions_repo import PushSubscriptionsRepo
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


async def _seed_user(session: AsyncSession) -> tuple[int, str]:
    """Insert invite + user. Return (user_id, jwt_token)."""
    code = _fresh_invite_code()
    token_raw = uuid4().hex * 2
    await session.execute(
        sa.text(
            "INSERT INTO invites (code, issued_by, expires_at, status, note) "
            "VALUES (:code, 'unsub-test', :exp, 'unused', 'push unsub')"
        ),
        {"code": code, "exp": datetime.now(UTC) + timedelta(days=7)},
    )
    result = await session.execute(
        sa.text(
            "INSERT INTO users (display_name, invite_code, device_token) "
            "VALUES (:name, :code, :token) RETURNING id, public_id"
        ),
        {"name": f"UnsubUser-{uuid4().hex[:6]}", "code": code, "token": token_raw},
    )
    row = result.mappings().one()
    user_id = int(row["id"])
    public_id = UUID(str(row["public_id"]))
    settings = get_settings()
    jwt, _ = JWTService(settings.jwt_secret).issue(public_id)
    return user_id, jwt


async def _seed_sub(session: AsyncSession, user_id: int) -> int:
    repo = PushSubscriptionsRepo(session)
    return await repo.upsert(
        user_id=user_id,
        endpoint=f"https://push.example/{uuid4().hex}",
        p256dh="pk",
        auth="ak",
        ua=None,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    url = require_real_db_url()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


# ---------------------------------------------------------------------------
# 1. 204 happy path
# ---------------------------------------------------------------------------


async def test_push_unsubscribe_deletes_owned_subscription(
    db_session: AsyncSession,
) -> None:
    user_id, jwt = await _seed_user(db_session)
    sub_id = await _seed_sub(db_session, user_id)

    async with AsyncClient(
        transport=ASGITransport(app=_app_with_session(db_session)),  # type: ignore[arg-type]
        base_url="http://test",
    ) as client:
        resp = await client.delete(
            f"/api/v1/push/subscriptions/{sub_id}",
            headers={"Authorization": f"Bearer {jwt}"},
        )
    assert resp.status_code == 204, resp.text
    assert resp.content == b""

    # Row must be gone
    gone = (
        await db_session.execute(
            sa.text("SELECT id FROM push_subscriptions WHERE id = :sid"),
            {"sid": sub_id},
        )
    ).one_or_none()
    assert gone is None

    # Cleanup user
    await db_session.execute(
        sa.text("DELETE FROM users WHERE id = :uid"), {"uid": user_id}
    )
    await db_session.commit()


# ---------------------------------------------------------------------------
# 2. 404 — subscription does not exist
# ---------------------------------------------------------------------------


async def test_push_unsubscribe_returns_404_for_missing_subscription(
    db_session: AsyncSession,
) -> None:
    _, jwt = await _seed_user(db_session)
    async with AsyncClient(
        transport=ASGITransport(app=_app_with_session(db_session)),  # type: ignore[arg-type]
        base_url="http://test",
    ) as client:
        resp = await client.delete(
            "/api/v1/push/subscriptions/999999999",
            headers={"Authorization": f"Bearer {jwt}"},
        )
    assert resp.status_code == 404, resp.text
    assert resp.json()["code"] == "subscription_not_found"


# ---------------------------------------------------------------------------
# 3. 404 — other user's subscription
# ---------------------------------------------------------------------------


async def test_push_unsubscribe_returns_404_for_other_users_subscription(
    db_session: AsyncSession,
) -> None:
    owner_id, _ = await _seed_user(db_session)
    _, requester_jwt = await _seed_user(db_session)
    sub_id = await _seed_sub(db_session, owner_id)

    async with AsyncClient(
        transport=ASGITransport(app=_app_with_session(db_session)),  # type: ignore[arg-type]
        base_url="http://test",
    ) as client:
        resp = await client.delete(
            f"/api/v1/push/subscriptions/{sub_id}",
            headers={"Authorization": f"Bearer {requester_jwt}"},
        )
    assert resp.status_code == 404, resp.text

    # Cleanup
    await db_session.execute(
        sa.text("DELETE FROM push_subscriptions WHERE id = :sid"), {"sid": sub_id}
    )
    await db_session.commit()


# ---------------------------------------------------------------------------
# 4. 401 — no JWT
# ---------------------------------------------------------------------------


async def test_push_unsubscribe_returns_401_without_jwt(
    db_session: AsyncSession,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=_app_with_session(db_session)),  # type: ignore[arg-type]
        base_url="http://test",
    ) as client:
        resp = await client.delete("/api/v1/push/subscriptions/1")
    assert resp.status_code == 401, resp.text
