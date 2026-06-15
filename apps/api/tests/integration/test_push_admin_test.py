"""Integration tests: POST /api/v1/internal/push/test (Module 011 T-009).

Uses a real Postgres for subscription seeding and patches
``WebPushSender.send`` to avoid real network calls.

Skips when DATABASE_URL is the conftest placeholder.

Coverage:
  1. 200 happy path — sends to all active subscriptions, returns summary.
  2. 200 filtered — sends only to a specific user's subscriptions.
  3. 401 — missing admin token.
  4. 403 — wrong admin token.
  5. 503 — VAPID keys not configured.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.infra.push_subscriptions_repo import PushSubscriptionsRepo
from app.infra.webpush_sender import SendOutcome, SendResult
from app.main import create_app
from app.settings import Settings, get_settings
from tests.fixtures import require_real_db_url

# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _fresh_invite_code() -> str:
    src = uuid4().hex.upper()
    valid = "".join(c for c in src if c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
    while len(valid) < 8:
        valid += "A"
    return f"{valid[:4]}-{valid[4:8]}"


@dataclass
class _Tracker:
    user_ids: list[int]
    invite_codes: list[str]


async def _seed_user(session: AsyncSession, tracker: _Tracker) -> tuple[int, UUID]:
    code = _fresh_invite_code()
    token = uuid4().hex * 2
    await session.execute(
        sa.text(
            "INSERT INTO invites (code, issued_by, expires_at, status, note) "
            "VALUES (:code, 'push-test-ep', :exp, 'unused', 'push-admin-test')"
        ),
        {"code": code, "exp": datetime.now(UTC) + timedelta(days=7)},
    )
    result = await session.execute(
        sa.text(
            "INSERT INTO users (display_name, invite_code, device_token) "
            "VALUES (:name, :code, :token) RETURNING id, public_id"
        ),
        {
            "name": f"PushAdminTestUser-{uuid4().hex[:6]}",
            "code": code,
            "token": token,
        },
    )
    row = result.mappings().one()
    uid = int(row["id"])
    pub = UUID(str(row["public_id"]))
    tracker.user_ids.append(uid)
    tracker.invite_codes.append(code)
    return uid, pub


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
# App factory helpers
# ---------------------------------------------------------------------------


def _settings_with_vapid(admin_token: str = "test-admin-token") -> Settings:
    return Settings.model_construct(
        database_url="postgresql+asyncpg://placeholder/placeholder",
        jwt_secret="test-secret",
        admin_token=admin_token,
        vapid_private_key="-----BEGIN PRIVATE KEY-----\nFAKE\n-----END PRIVATE KEY-----",
        vapid_public_key="BFakePublicKey",
        vapid_subject="mailto:ops@example.com",
    )


def _settings_without_vapid() -> Settings:
    return Settings.model_construct(
        database_url="postgresql+asyncpg://placeholder/placeholder",
        jwt_secret="test-secret",
        admin_token="test-admin-token",
        vapid_private_key=None,
        vapid_public_key=None,
        vapid_subject="mailto:ops@example.com",
    )


def _app_with_session_and_settings(
    session: AsyncSession, settings: Settings
) -> object:
    app = create_app()

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _session_override
    app.dependency_overrides[get_settings] = lambda: settings
    return app


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


@pytest.fixture
async def tracker(db_session: AsyncSession) -> AsyncIterator[_Tracker]:
    t = _Tracker(user_ids=[], invite_codes=[])
    yield t
    if t.user_ids:
        await db_session.execute(
            sa.text("DELETE FROM push_subscriptions WHERE user_id = ANY(:u)"),
            {"u": t.user_ids},
        )
        await db_session.execute(
            sa.text("DELETE FROM users WHERE id = ANY(:u)"),
            {"u": t.user_ids},
        )
    if t.invite_codes:
        await db_session.execute(
            sa.text("DELETE FROM invites WHERE code = ANY(:c)"),
            {"c": t.invite_codes},
        )
    await db_session.commit()


_ADMIN_HDR = {"Authorization": "Bearer test-admin-token"}


# ---------------------------------------------------------------------------
# 1. 200 — happy path (all subs)
# ---------------------------------------------------------------------------


async def test_push_admin_test_sends_to_all_subs(
    db_session: AsyncSession,
    tracker: _Tracker,
) -> None:
    uid, _ = await _seed_user(db_session, tracker)
    sub_id = await _seed_sub(db_session, uid)
    await db_session.commit()

    mock_outcome = SendOutcome(
        subscription_id=sub_id, result=SendResult.SUCCESS, status_code=201
    )

    app = _app_with_session_and_settings(db_session, _settings_with_vapid())
    with patch(
        "app.infra.webpush_sender.WebPushSender.send",
        new=AsyncMock(return_value=mock_outcome),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),  # type: ignore[arg-type]
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/v1/internal/push/test",
                json={},
                headers=_ADMIN_HDR,
            )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["sent"] == 1
    assert data["failed"] == 0
    assert data["gone"] == 0
    assert data["subscription_count"] == 1


# ---------------------------------------------------------------------------
# 2. 200 — filtered to one user
# ---------------------------------------------------------------------------


async def test_push_admin_test_filtered_to_user(
    db_session: AsyncSession,
    tracker: _Tracker,
) -> None:
    uid, pub = await _seed_user(db_session, tracker)
    other_uid, _ = await _seed_user(db_session, tracker)
    sub_id = await _seed_sub(db_session, uid)
    await _seed_sub(db_session, other_uid)
    await db_session.commit()

    mock_outcome = SendOutcome(
        subscription_id=sub_id, result=SendResult.SUCCESS, status_code=201
    )

    app = _app_with_session_and_settings(db_session, _settings_with_vapid())
    with patch(
        "app.infra.webpush_sender.WebPushSender.send",
        new=AsyncMock(return_value=mock_outcome),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app),  # type: ignore[arg-type]
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/v1/internal/push/test",
                json={"user_public_id": str(pub)},
                headers=_ADMIN_HDR,
            )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Only the target user's sub was processed
    assert data["subscription_count"] == 1
    assert data["sent"] == 1


# ---------------------------------------------------------------------------
# 3. 401 — missing admin token
# ---------------------------------------------------------------------------


async def test_push_admin_test_returns_401_without_token(
    db_session: AsyncSession,
) -> None:
    app = _app_with_session_and_settings(db_session, _settings_with_vapid())
    async with AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as client:
        resp = await client.post("/api/v1/internal/push/test", json={})
    assert resp.status_code == 401, resp.text
    assert resp.json()["code"] == "missing_admin_token"


# ---------------------------------------------------------------------------
# 4. 403 — wrong admin token
# ---------------------------------------------------------------------------


async def test_push_admin_test_returns_403_with_wrong_token(
    db_session: AsyncSession,
) -> None:
    app = _app_with_session_and_settings(db_session, _settings_with_vapid())
    async with AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/api/v1/internal/push/test",
            json={},
            headers={"Authorization": "Bearer wrong-token"},
        )
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == "bad_admin_token"


# ---------------------------------------------------------------------------
# 5. 503 — VAPID keys not configured
# ---------------------------------------------------------------------------


async def test_push_admin_test_returns_503_without_vapid_keys(
    db_session: AsyncSession,
) -> None:
    app = _app_with_session_and_settings(db_session, _settings_without_vapid())
    async with AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/api/v1/internal/push/test",
            json={},
            headers=_ADMIN_HDR,
        )
    assert resp.status_code == 503, resp.text
    assert resp.json()["code"] == "push_not_configured"
