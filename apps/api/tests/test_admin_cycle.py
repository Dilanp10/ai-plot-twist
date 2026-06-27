"""Tests — Module 014 T-002: GET /api/v1/admin/cycle."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.domain.admin_auth import issue_admin_jwt
from app.infra.cycles_repo import CycleRow
from app.main import create_app
from app.settings import Settings, get_settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(with_r2: bool = True) -> Settings:
    return Settings(  # type: ignore[call-arg]
        database_url="postgresql+asyncpg://x:x@localhost/x",
        jwt_secret="test-secret-that-is-long-enough-for-jwt",
        admin_password="dilan",
        r2_public_base_url="https://cdn.example.com" if with_r2 else None,
    )


def _make_client(settings: Settings) -> TestClient:
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def _admin_headers(jwt_secret: str) -> dict[str, str]:
    token = issue_admin_jwt(jwt_secret)
    return {"Authorization": f"Bearer {token}"}


_FAKE_CYCLE = CycleRow(
    id=3,
    season_id=1,
    chapter_id=5,
    next_chapter_id=None,
    state="GENERACION",
    state_entered_at=datetime(2026, 6, 27, 23, 0, 0, tzinfo=timezone.utc),
    cycle_date=date(2026, 6, 27),
)


# ---------------------------------------------------------------------------
# Auth guard tests (no DB needed)
# ---------------------------------------------------------------------------


def test_cycle_no_token() -> None:
    client = _make_client(_settings())
    resp = client.get("/api/v1/admin/cycle")
    assert resp.status_code == 401


def test_cycle_bad_token() -> None:
    client = _make_client(_settings())
    resp = client.get(
        "/api/v1/admin/cycle",
        headers={"Authorization": "Bearer garbage"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# No active cycle → 404
# ---------------------------------------------------------------------------


def test_cycle_no_active_cycle() -> None:
    settings = _settings()
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings

    # Mock CyclesRepo.get_active to return None
    with patch("app.api.admin.CyclesRepo") as mock_repo_cls:
        mock_repo = MagicMock()
        mock_repo.get_active = AsyncMock(return_value=None)
        mock_repo_cls.return_value = mock_repo

        client = TestClient(app)
        resp = client.get(
            "/api/v1/admin/cycle",
            headers=_admin_headers(settings.jwt_secret),
        )

    assert resp.status_code == 404
    assert resp.json()["code"] == "no_active_cycle"


# ---------------------------------------------------------------------------
# Active cycle with no approved twists → winner is null
# ---------------------------------------------------------------------------


def test_cycle_no_winner() -> None:
    settings = _settings()
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings

    with patch("app.api.admin.CyclesRepo") as mock_repo_cls, patch(
        "app.api.admin._fetch_winner", new=AsyncMock(return_value=None)
    ):
        mock_repo = MagicMock()
        mock_repo.get_active = AsyncMock(return_value=_FAKE_CYCLE)
        mock_repo_cls.return_value = mock_repo

        client = TestClient(app)
        resp = client.get(
            "/api/v1/admin/cycle",
            headers=_admin_headers(settings.jwt_secret),
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["cycle_state"] == "GENERACION"
    assert data["chapter_id"] == 5
    assert data["winner"] is None


# ---------------------------------------------------------------------------
# Active cycle with winner → full response
# ---------------------------------------------------------------------------


def test_cycle_with_winner() -> None:
    from app.api.admin import WinnerInfo

    settings = _settings()
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings

    fake_winner = WinnerInfo(
        twist_text="Messi desafía a CR7 en el último minuto",
        vote_count=7,
        author_display_name="Dilan",
        character_slug="messi",
        character_name="Lionel Messi",
        character_photo_url="https://cdn.example.com/static/characters/messi.webp",
    )

    with patch("app.api.admin.CyclesRepo") as mock_repo_cls, patch(
        "app.api.admin._fetch_winner", new=AsyncMock(return_value=fake_winner)
    ):
        mock_repo = MagicMock()
        mock_repo.get_active = AsyncMock(return_value=_FAKE_CYCLE)
        mock_repo_cls.return_value = mock_repo

        client = TestClient(app)
        resp = client.get(
            "/api/v1/admin/cycle",
            headers=_admin_headers(settings.jwt_secret),
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["cycle_state"] == "GENERACION"
    assert data["winner"]["twist_text"] == "Messi desafía a CR7 en el último minuto"
    assert data["winner"]["vote_count"] == 7
    assert data["winner"]["character_slug"] == "messi"
    assert data["winner"]["character_photo_url"].endswith("messi.webp")
