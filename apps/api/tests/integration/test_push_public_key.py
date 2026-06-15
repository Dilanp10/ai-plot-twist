"""Integration tests: GET /api/v1/push/public-key (Module 011 T-007).

No DB required — the endpoint only reads settings.

Coverage:
  1. 200 with a configured VAPID public key.
  2. 503 when VAPID_PUBLIC_KEY is absent from settings.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from httpx import ASGITransport, AsyncClient

from app.db import get_session
from app.main import create_app
from app.settings import Settings, get_settings

_FAKE_PUB_KEY = "BNy4zXkfGpbSHPcDxFake0T9AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def _app_with_settings(settings: Settings) -> object:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings

    # Provide a no-op session so the app doesn't try to connect to Postgres.
    async def _no_session() -> AsyncIterator[None]:
        yield None

    app.dependency_overrides[get_session] = _no_session
    return app


def _settings(vapid_public_key: str | None) -> Settings:
    return Settings.model_construct(
        vapid_public_key=vapid_public_key,
        vapid_private_key=None,
        vapid_subject="mailto:ops@example.com",
        database_url="postgresql+asyncpg://placeholder/placeholder",
        jwt_secret="test-secret",
    )


# ---------------------------------------------------------------------------
# 1. 200 — key is configured
# ---------------------------------------------------------------------------


async def test_get_push_public_key_returns_200_with_key() -> None:
    app = _app_with_settings(_settings(_FAKE_PUB_KEY))
    async with AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/v1/push/public-key")
    assert resp.status_code == 200, resp.text
    assert resp.json()["public_key"] == _FAKE_PUB_KEY


# ---------------------------------------------------------------------------
# 2. 503 — key is not set
# ---------------------------------------------------------------------------


async def test_get_push_public_key_returns_503_when_not_configured() -> None:
    app = _app_with_settings(_settings(None))
    async with AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as client:
        resp = await client.get("/api/v1/push/public-key")
    assert resp.status_code == 503, resp.text
    assert resp.json()["code"] == "push_not_configured"
