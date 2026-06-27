"""Tests — Module 014 T-003: POST /admin/chapters/{id}/video-upload-url."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.domain.admin_auth import issue_admin_jwt
from app.main import create_app
from app.settings import Settings, get_settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(with_r2: bool = True) -> Settings:
    return Settings(  # type: ignore[call-arg]
        database_url="postgresql+asyncpg://x:x@localhost/x",
        jwt_secret="test-secret-that-is-long-enough-32ch",
        admin_password="dilan",
        r2_account_id="acct123" if with_r2 else None,
        r2_access_key_id="keyid" if with_r2 else None,
        r2_secret_access_key="secret" if with_r2 else None,
        r2_bucket="my-bucket" if with_r2 else None,
        r2_public_base_url="https://cdn.example.com" if with_r2 else None,
    )


def _make_client(settings: Settings) -> TestClient:
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def _admin_headers(jwt_secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {issue_admin_jwt(jwt_secret)}"}


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


def test_upload_url_no_token() -> None:
    client = _make_client(_settings())
    resp = client.post("/api/v1/admin/chapters/5/video-upload-url")
    assert resp.status_code == 401


def test_upload_url_bad_token() -> None:
    client = _make_client(_settings())
    resp = client.post(
        "/api/v1/admin/chapters/5/video-upload-url",
        headers={"Authorization": "Bearer garbage"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# R2 not configured → 503
# ---------------------------------------------------------------------------


def test_upload_url_r2_not_configured() -> None:
    settings = _settings(with_r2=False)
    client = _make_client(settings)
    resp = client.post(
        "/api/v1/admin/chapters/5/video-upload-url",
        headers=_admin_headers(settings.jwt_secret),
    )
    assert resp.status_code == 503
    assert resp.json()["code"] == "r2_not_configured"


# ---------------------------------------------------------------------------
# Success — presigned URL returned
# ---------------------------------------------------------------------------


def test_upload_url_success() -> None:
    settings = _settings(with_r2=True)
    client = _make_client(settings)

    fake_presigned = "https://acct123.r2.cloudflarestorage.com/my-bucket/chapters/5/video.mp4?X-Amz-Signature=abc"

    with patch("app.api.admin.R2Uploader") as mock_uploader_cls:
        mock_uploader = MagicMock()
        mock_uploader.generate_presigned_put_url.return_value = (
            fake_presigned,
            "https://cdn.example.com/chapters/5/video.mp4",
        )
        mock_uploader_cls.return_value = mock_uploader

        resp = client.post(
            "/api/v1/admin/chapters/5/video-upload-url",
            headers=_admin_headers(settings.jwt_secret),
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["upload_url"] == fake_presigned
    assert data["public_url"] == "https://cdn.example.com/chapters/5/video.mp4"
    assert data["key"] == "chapters/5/video.mp4"

    # Verify the uploader was called with the correct key
    mock_uploader.generate_presigned_put_url.assert_called_once_with(
        "chapters/5/video.mp4"
    )


def test_upload_url_key_includes_chapter_id() -> None:
    """Key must be chapters/{chapter_id}/video.mp4."""
    settings = _settings(with_r2=True)
    client = _make_client(settings)

    with patch("app.api.admin.R2Uploader") as mock_uploader_cls:
        mock_uploader = MagicMock()
        mock_uploader.generate_presigned_put_url.return_value = (
            "https://r2.example.com/signed",
            "https://cdn.example.com/chapters/42/video.mp4",
        )
        mock_uploader_cls.return_value = mock_uploader

        resp = client.post(
            "/api/v1/admin/chapters/42/video-upload-url",
            headers=_admin_headers(settings.jwt_secret),
        )

    assert resp.status_code == 200
    assert resp.json()["key"] == "chapters/42/video.mp4"
    mock_uploader.generate_presigned_put_url.assert_called_once_with(
        "chapters/42/video.mp4"
    )


# ---------------------------------------------------------------------------
# T-004: PUT /api/v1/admin/chapters/{chapter_id}/video
# ---------------------------------------------------------------------------

from datetime import date, datetime, timezone
from app.infra.cycles_repo import CycleRow

_FAKE_CYCLE_GENERACION = CycleRow(
    id=3,
    season_id=1,
    chapter_id=5,
    next_chapter_id=6,
    state="GENERACION",
    state_entered_at=datetime(2026, 6, 27, 23, 0, 0, tzinfo=timezone.utc),
    cycle_date=date(2026, 6, 27),
)

_FAKE_CYCLE_RECEPCION = CycleRow(
    id=3,
    season_id=1,
    chapter_id=5,
    next_chapter_id=None,
    state="RECEPCION_IDEAS",
    state_entered_at=datetime(2026, 6, 27, 12, 0, 0, tzinfo=timezone.utc),
    cycle_date=date(2026, 6, 27),
)


def test_confirm_video_no_token() -> None:
    client = _make_client(_settings())
    resp = client.put(
        "/api/v1/admin/chapters/5/video",
        json={"video_url": "https://cdn.example.com/chapters/5/video.mp4"},
    )
    assert resp.status_code == 401


def test_confirm_video_wrong_state() -> None:
    from unittest.mock import AsyncMock, MagicMock

    settings = _settings()
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings

    with patch("app.api.admin.CyclesRepo") as mock_repo_cls:
        mock_repo = MagicMock()
        mock_repo.get_active = AsyncMock(return_value=_FAKE_CYCLE_RECEPCION)
        mock_repo_cls.return_value = mock_repo

        client = TestClient(app)
        resp = client.put(
            "/api/v1/admin/chapters/5/video",
            headers=_admin_headers(settings.jwt_secret),
            json={"video_url": "https://cdn.example.com/chapters/5/video.mp4"},
        )

    assert resp.status_code == 403
    assert resp.json()["code"] == "wrong_cycle_state"


def test_confirm_video_no_active_cycle() -> None:
    from unittest.mock import AsyncMock, MagicMock

    settings = _settings()
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings

    with patch("app.api.admin.CyclesRepo") as mock_repo_cls:
        mock_repo = MagicMock()
        mock_repo.get_active = AsyncMock(return_value=None)
        mock_repo_cls.return_value = mock_repo

        client = TestClient(app)
        resp = client.put(
            "/api/v1/admin/chapters/5/video",
            headers=_admin_headers(settings.jwt_secret),
            json={"video_url": "https://cdn.example.com/chapters/5/video.mp4"},
        )

    assert resp.status_code == 404
    assert resp.json()["code"] == "no_active_cycle"


def test_confirm_video_chapter_not_found() -> None:
    from unittest.mock import AsyncMock, MagicMock

    settings = _settings()
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings

    with patch("app.api.admin.CyclesRepo") as mock_cycles_cls, patch(
        "app.api.admin.ChaptersRepo"
    ) as mock_chapters_cls:
        mock_cycles = MagicMock()
        mock_cycles.get_active = AsyncMock(return_value=_FAKE_CYCLE_GENERACION)
        mock_cycles_cls.return_value = mock_cycles

        mock_chapters = MagicMock()
        mock_chapters.set_video_url = AsyncMock(return_value=False)
        mock_chapters_cls.return_value = mock_chapters

        client = TestClient(app)
        resp = client.put(
            "/api/v1/admin/chapters/999/video",
            headers=_admin_headers(settings.jwt_secret),
            json={"video_url": "https://cdn.example.com/chapters/999/video.mp4"},
        )

    assert resp.status_code == 404
    assert resp.json()["code"] == "chapter_not_found"


def test_confirm_video_success() -> None:
    from unittest.mock import AsyncMock, MagicMock

    settings = _settings()
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings

    video_url = "https://cdn.example.com/chapters/6/video.mp4"

    with patch("app.api.admin.CyclesRepo") as mock_cycles_cls, patch(
        "app.api.admin.ChaptersRepo"
    ) as mock_chapters_cls:
        mock_cycles = MagicMock()
        mock_cycles.get_active = AsyncMock(return_value=_FAKE_CYCLE_GENERACION)
        mock_cycles_cls.return_value = mock_cycles

        mock_chapters = MagicMock()
        mock_chapters.set_video_url = AsyncMock(return_value=True)
        mock_chapters_cls.return_value = mock_chapters

        client = TestClient(app)
        resp = client.put(
            "/api/v1/admin/chapters/6/video",
            headers=_admin_headers(settings.jwt_secret),
            json={"video_url": video_url},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["chapter_id"] == 6
    assert data["video_url"] == video_url
    mock_chapters.set_video_url.assert_called_once_with(6, video_url)
