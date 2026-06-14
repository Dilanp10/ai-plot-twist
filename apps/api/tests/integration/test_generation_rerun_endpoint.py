"""Integration tests: POST /api/v1/internal/generation/rerun (T-012).

All-mocked endpoint tests. The pipeline itself is exhaustively covered
by ``tests/integration/generation_pipeline/`` — here we verify only
the endpoint composition:

  1. 401 missing Authorization header.
  2. 403 bad bearer token.
  3. 503 generation_pipeline dependencies not wired.
  4. 404 unknown chapter_id.
  5. Happy path: returns the new chapter UUID + summary, calls the
     pipeline with ``skip_cycle_transition=True``.
  6. Happy path: ``_delete_existing_next_chapter`` is awaited before
     the pipeline runs (so a re-run does not collide on UNIQUE).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import internal_generation_rerun as endpoint_mod
from app.domain.generation_pipeline import GenerationSummary
from app.domain.scriptwriter import Scriptwriter
from app.errors import ProblemDetail
from app.infra.r2_uploader import R2Uploader
from app.providers.image import ImageProviderRouter

_ADMIN_TOKEN = "test-admin-token-T012"
_PATH = "/api/v1/internal/generation/rerun"
_SOURCE_CHAPTER_UUID = uuid4()
_NEW_CHAPTER_UUID = uuid4()
_INTERNAL_ID = 99
_NEW_INTERNAL_ID = 100


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Set ADMIN_TOKEN + minimal generation settings, reset Settings cache."""
    monkeypatch.setenv("ADMIN_TOKEN", _ADMIN_TOKEN)
    monkeypatch.setenv(
        "GENERATION_PLACEHOLDER_URL",
        "https://assets.example.com/static/placeholder.webp",
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://placeholder/db")
    monkeypatch.setenv("JWT_SECRET", "placeholder")

    from app.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _build_app(*, wire_deps: bool) -> FastAPI:
    """Build a FastAPI app, optionally with mocked generation deps on state."""
    from app.db import get_session
    from app.main import create_app

    app = create_app()

    async def _no_session() -> AsyncIterator[AsyncSession]:
        yield AsyncMock(spec=AsyncSession)

    app.dependency_overrides[get_session] = _no_session

    if wire_deps:
        app.state.scriptwriter = AsyncMock(spec=Scriptwriter)
        app.state.image_router = AsyncMock(spec=ImageProviderRouter)
        app.state.r2_uploader = AsyncMock(spec=R2Uploader)
    return app


async def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


def _summary() -> GenerationSummary:
    return GenerationSummary(
        new_chapter_id=_NEW_INTERNAL_ID,
        new_chapter_public_id=_NEW_CHAPTER_UUID,
        status="ready",
        panels_ok=3,
        panels_degraded=0,
        duration_ms=12345,
        has_winner=True,
    )


# ---------------------------------------------------------------------------
# 1. Auth — 401 missing Authorization
# ---------------------------------------------------------------------------


async def test_rerun_missing_authorization_returns_401(
    admin_env: None,
) -> None:
    app = _build_app(wire_deps=True)
    client = await _client(app)
    async with client:
        resp = await client.post(
            _PATH,
            json={"chapter_id": str(_SOURCE_CHAPTER_UUID)},
        )

    assert resp.status_code == 401
    assert resp.json()["code"] == "missing_admin_token"


# ---------------------------------------------------------------------------
# 2. Auth — 403 wrong token
# ---------------------------------------------------------------------------


async def test_rerun_bad_token_returns_403(admin_env: None) -> None:
    app = _build_app(wire_deps=True)
    client = await _client(app)
    async with client:
        resp = await client.post(
            _PATH,
            json={"chapter_id": str(_SOURCE_CHAPTER_UUID)},
            headers={"Authorization": "Bearer not-the-real-token"},
        )

    assert resp.status_code == 403
    assert resp.json()["code"] == "bad_admin_token"


# ---------------------------------------------------------------------------
# 3. Deps not wired — 503
# ---------------------------------------------------------------------------


async def test_rerun_deps_unwired_returns_503(admin_env: None) -> None:
    app = _build_app(wire_deps=False)
    client = await _client(app)
    async with client:
        resp = await client.post(
            _PATH,
            json={"chapter_id": str(_SOURCE_CHAPTER_UUID)},
            headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
        )

    assert resp.status_code == 503
    assert resp.json()["code"] == "generation_pipeline_unavailable"


# ---------------------------------------------------------------------------
# 4. Unknown chapter_id — 404
# ---------------------------------------------------------------------------


async def test_rerun_unknown_chapter_returns_404(admin_env: None) -> None:
    app = _build_app(wire_deps=True)

    with patch.object(
        endpoint_mod,
        "_resolve_internal_chapter_id",
        new=AsyncMock(
            side_effect=ProblemDetail(
                status=404,
                code="chapter_not_found",
                title="Chapter not found",
                detail="missing",
            )
        ),
    ):
        client = await _client(app)
        async with client:
            resp = await client.post(
                _PATH,
                json={"chapter_id": str(_SOURCE_CHAPTER_UUID)},
                headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
            )

    assert resp.status_code == 404
    assert resp.json()["code"] == "chapter_not_found"


# ---------------------------------------------------------------------------
# 5. Happy path
# ---------------------------------------------------------------------------


async def test_rerun_happy_path_returns_new_chapter_uuid(
    admin_env: None,
) -> None:
    app = _build_app(wire_deps=True)
    pipeline_mock = AsyncMock(return_value=_summary())

    with (
        patch.object(
            endpoint_mod,
            "_resolve_internal_chapter_id",
            new=AsyncMock(return_value=_INTERNAL_ID),
        ),
        patch.object(
            endpoint_mod,
            "_delete_existing_next_chapter",
            new=AsyncMock(),
        ),
        patch.object(endpoint_mod, "run_generation_pipeline", new=pipeline_mock),
    ):
        client = await _client(app)
        async with client:
            resp = await client.post(
                _PATH,
                json={"chapter_id": str(_SOURCE_CHAPTER_UUID)},
                headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
            )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert UUID(body["source_chapter_id"]) == _SOURCE_CHAPTER_UUID
    assert UUID(body["new_chapter_id"]) == _NEW_CHAPTER_UUID
    assert body["status"] == "ready"
    assert body["panels_ok"] == 3
    assert body["panels_degraded"] == 0
    assert body["duration_ms"] == 12345
    assert body["has_winner"] is True


# ---------------------------------------------------------------------------
# 6. Pipeline called with skip_cycle_transition=True + delete runs first
# ---------------------------------------------------------------------------


async def test_rerun_calls_pipeline_with_skip_cycle_transition(
    admin_env: None,
) -> None:
    app = _build_app(wire_deps=True)
    pipeline_mock = AsyncMock(return_value=_summary())
    delete_mock = AsyncMock()

    call_order: list[str] = []

    async def _delete_track(*_args: object, **_kwargs: object) -> None:
        call_order.append("delete")

    async def _pipeline_track(
        *_args: object, **_kwargs: object
    ) -> GenerationSummary:
        call_order.append("pipeline")
        return _summary()

    delete_mock.side_effect = _delete_track
    pipeline_mock.side_effect = _pipeline_track

    with (
        patch.object(
            endpoint_mod,
            "_resolve_internal_chapter_id",
            new=AsyncMock(return_value=_INTERNAL_ID),
        ),
        patch.object(
            endpoint_mod,
            "_delete_existing_next_chapter",
            new=delete_mock,
        ),
        patch.object(endpoint_mod, "run_generation_pipeline", new=pipeline_mock),
    ):
        client = await _client(app)
        async with client:
            resp = await client.post(
                _PATH,
                json={"chapter_id": str(_SOURCE_CHAPTER_UUID)},
                headers={"Authorization": f"Bearer {_ADMIN_TOKEN}"},
            )

    assert resp.status_code == 200, resp.text
    assert call_order == ["delete", "pipeline"]
    assert pipeline_mock.call_args.kwargs["skip_cycle_transition"] is True
    assert pipeline_mock.call_args.args[0] == _INTERNAL_ID
