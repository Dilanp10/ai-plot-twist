"""Unit tests: GET /api/v1/characters (module 013 / Task T-005).

``CharactersRepo`` is patched at the class level; ``require_user`` and
``get_session`` are overridden via ``dependency_overrides``. No DB required.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.infra.characters_repo import CharacterRow
from app.main import create_app
from app.middleware.jwt_auth import require_user
from app.settings import get_settings

_URL = "/api/v1/characters"
_BASE = "https://r2.example.com"

_ROWS = [
    CharacterRow(
        id=1,
        slug="messi",
        display_name="Lionel Messi",
        photo_r2_key="static/characters/messi.webp",
        aspect_ratio="1:1",
    ),
    CharacterRow(
        id=2,
        slug="bad-bunny",
        display_name="Bad Bunny",
        photo_r2_key="static/characters/bad-bunny.webp",
        aspect_ratio="1:1",
    ),
]


async def _mock_session() -> AsyncSession:  # type: ignore[misc]
    yield AsyncMock(spec=AsyncSession)


def _mock_user() -> MagicMock:
    u = MagicMock()
    u.id = 99
    return u


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("TICK_SECRET", "test-tick")
    monkeypatch.setenv("JWT_SECRET", "test-jwt")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://mock/mock")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", _BASE)
    get_settings.cache_clear()

    a = create_app()
    a.dependency_overrides[get_session] = _mock_session
    a.dependency_overrides[require_user] = _mock_user
    try:
        yield a
    finally:
        get_settings.cache_clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_200_returns_active_characters(client: TestClient) -> None:
    with patch("app.api.characters.CharactersRepo") as MockRepo:
        MockRepo.return_value.list_active = AsyncMock(return_value=_ROWS)
        r = client.get(_URL)

    assert r.status_code == 200
    data = r.json()
    assert len(data["characters"]) == 2
    assert data["characters"][0]["slug"] == "messi"
    assert data["characters"][1]["slug"] == "bad-bunny"


def test_200_photo_url_is_fully_qualified(client: TestClient) -> None:
    with patch("app.api.characters.CharactersRepo") as MockRepo:
        MockRepo.return_value.list_active = AsyncMock(return_value=_ROWS[:1])
        r = client.get(_URL)

    url = r.json()["characters"][0]["photo_url"]
    assert url == f"{_BASE}/static/characters/messi.webp"


def test_200_includes_etag_and_cache_headers(client: TestClient) -> None:
    with patch("app.api.characters.CharactersRepo") as MockRepo:
        MockRepo.return_value.list_active = AsyncMock(return_value=_ROWS)
        r = client.get(_URL)

    assert r.status_code == 200
    assert "ETag" in r.headers
    assert r.headers["ETag"].startswith('"')
    assert "private" in r.headers.get("Cache-Control", "")
    assert "max-age=300" in r.headers.get("Cache-Control", "")


def test_200_empty_catalog(client: TestClient) -> None:
    with patch("app.api.characters.CharactersRepo") as MockRepo:
        MockRepo.return_value.list_active = AsyncMock(return_value=[])
        r = client.get(_URL)

    assert r.status_code == 200
    assert r.json()["characters"] == []
    assert "ETag" in r.headers


# ---------------------------------------------------------------------------
# 304 Not Modified
# ---------------------------------------------------------------------------


def test_304_on_matching_etag(client: TestClient) -> None:
    with patch("app.api.characters.CharactersRepo") as MockRepo:
        MockRepo.return_value.list_active = AsyncMock(return_value=_ROWS)
        first = client.get(_URL)
        etag = first.headers["ETag"]
        r = client.get(_URL, headers={"If-None-Match": etag})

    assert r.status_code == 304
    assert r.content == b""


def test_200_on_stale_etag(client: TestClient) -> None:
    with patch("app.api.characters.CharactersRepo") as MockRepo:
        MockRepo.return_value.list_active = AsyncMock(return_value=_ROWS)
        r = client.get(_URL, headers={"If-None-Match": '"stale-etag"'})

    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


def test_401_without_jwt(app: FastAPI) -> None:
    del app.dependency_overrides[require_user]
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get(_URL)
    assert r.status_code == 401
