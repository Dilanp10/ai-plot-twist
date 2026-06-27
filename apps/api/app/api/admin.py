"""Admin router — panel authentication and cycle management.

Endpoints
---------
POST /api/v1/admin/auth   (T-001) — password → admin JWT
GET  /api/v1/admin/cycle  (T-002) — winning idea + character info
POST /api/v1/admin/chapters/{chapter_id}/video-upload-url  (T-003)
PUT  /api/v1/admin/chapters/{chapter_id}/video             (T-004)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from fastapi import Depends
from fastapi.routing import APIRouter
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.domain.admin_auth import (
    issue_admin_jwt,
    require_admin_jwt,
    verify_admin_password,
)
from app.errors import ProblemDetail
from app.infra.chapters_repo import ChaptersRepo
from app.infra.cycles_repo import CyclesRepo
from app.infra.r2_uploader import R2Uploader
from app.settings import Settings, get_settings

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AdminAuthRequest(BaseModel):
    password: str


class AdminAuthResponse(BaseModel):
    token: str


class WinnerInfo(BaseModel):
    twist_text: str
    vote_count: int
    author_display_name: str
    character_slug: str
    character_name: str
    character_photo_url: str


class AdminCycleResponse(BaseModel):
    cycle_state: str
    cycle_date: date
    state_entered_at: datetime
    chapter_id: int
    next_chapter_id: int | None
    winner: WinnerInfo | None


class VideoUploadUrlResponse(BaseModel):
    upload_url: str
    public_url: str
    key: str


class VideoConfirmRequest(BaseModel):
    video_url: str


class VideoConfirmResponse(BaseModel):
    chapter_id: int
    video_url: str


# ---------------------------------------------------------------------------
# Internal query — winner + character for a chapter (single round-trip)
# ---------------------------------------------------------------------------

_WINNER_WITH_CHARACTER_SQL = sa.text(
    "SELECT"
    "  t.content          AS twist_text,"
    "  u.display_name     AS author_display_name,"
    "  COUNT(v.id)        AS vote_count,"
    "  c.slug             AS character_slug,"
    "  c.display_name     AS character_name,"
    "  c.photo_r2_key     AS character_photo_r2_key"
    " FROM twists t"
    " JOIN users u        ON u.id = t.user_id"
    " LEFT JOIN votes v   ON v.twist_id = t.id"
    " LEFT JOIN characters c ON c.id = t.character_id"
    " WHERE t.chapter_id = :chapter_id"
    "   AND t.status = 'approved'"
    " GROUP BY t.id, t.content, u.display_name, c.slug, c.display_name, c.photo_r2_key"
    " ORDER BY COUNT(v.id) DESC, t.submitted_at ASC, t.id ASC"
    " LIMIT 1"
)


async def _fetch_winner(
    session: AsyncSession,
    chapter_id: int,
    r2_public_base_url: str,
) -> WinnerInfo | None:
    result = await session.execute(
        _WINNER_WITH_CHARACTER_SQL, {"chapter_id": chapter_id}
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    photo_url = f"{r2_public_base_url.rstrip('/')}/{row['character_photo_r2_key']}"
    return WinnerInfo(
        twist_text=str(row["twist_text"]),
        vote_count=int(row["vote_count"]),
        author_display_name=str(row["author_display_name"]),
        character_slug=str(row["character_slug"]),
        character_name=str(row["character_name"]),
        character_photo_url=photo_url,
    )


# ---------------------------------------------------------------------------
# T-001: POST /api/v1/admin/auth
# ---------------------------------------------------------------------------


@router.post("/auth", response_model=AdminAuthResponse)
async def admin_auth(
    body: AdminAuthRequest,
    settings: Settings = Depends(get_settings),
) -> AdminAuthResponse:
    """Exchange the admin password for a short-lived JWT (8 h).

    Returns 401 if ADMIN_PASSWORD is not set on the server.
    Returns 403 if the password is wrong.
    """
    if not settings.admin_password:
        raise ProblemDetail(
            status=401,
            code="admin_password_not_configured",
            title="Unauthorized",
            detail="ADMIN_PASSWORD is not configured on the server.",
        )

    if not verify_admin_password(body.password, settings):
        raise ProblemDetail(
            status=403,
            code="wrong_admin_password",
            title="Forbidden",
            detail="Incorrect password.",
        )

    token = issue_admin_jwt(settings.jwt_secret)
    return AdminAuthResponse(token=token)


# ---------------------------------------------------------------------------
# T-002: GET /api/v1/admin/cycle
# ---------------------------------------------------------------------------


@router.get("/cycle", response_model=AdminCycleResponse)
async def admin_get_cycle(
    _: None = Depends(require_admin_jwt),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AdminCycleResponse:
    """Return the active cycle state + winning twist + character.

    Returns 404 if no active season / cycle exists.
    ``winner`` is null when no approved twists exist for the current chapter
    (e.g. early in RECEPCION_IDEAS before anyone has submitted).
    """
    cycles_repo = CyclesRepo(db)
    cycle = await cycles_repo.get_active()
    if cycle is None:
        raise ProblemDetail(
            status=404,
            code="no_active_cycle",
            title="Not Found",
            detail="No active cycle found.",
        )

    winner = None
    if settings.r2_public_base_url:
        winner = await _fetch_winner(db, cycle.chapter_id, settings.r2_public_base_url)

    return AdminCycleResponse(
        cycle_state=cycle.state,
        cycle_date=cycle.cycle_date,
        state_entered_at=cycle.state_entered_at,
        chapter_id=cycle.chapter_id,
        next_chapter_id=cycle.next_chapter_id,
        winner=winner,
    )


# ---------------------------------------------------------------------------
# Internal helper — build R2Uploader from settings (used by T-003 / T-004)
# ---------------------------------------------------------------------------


def _build_r2_uploader(settings: Settings) -> R2Uploader:
    """Instantiate R2Uploader from settings. Raises ProblemDetail 503 if incomplete."""
    missing = [
        name
        for name, val in (
            ("R2_ACCOUNT_ID", settings.r2_account_id),
            ("R2_ACCESS_KEY_ID", settings.r2_access_key_id),
            ("R2_SECRET_ACCESS_KEY", settings.r2_secret_access_key),
            ("R2_BUCKET", settings.r2_bucket),
            ("R2_PUBLIC_BASE_URL", settings.r2_public_base_url),
        )
        if not val
    ]
    if missing:
        raise ProblemDetail(
            status=503,
            code="r2_not_configured",
            title="Service Unavailable",
            detail=f"R2 not fully configured. Missing: {', '.join(missing)}",
        )
    assert settings.r2_account_id is not None
    assert settings.r2_access_key_id is not None
    assert settings.r2_secret_access_key is not None
    assert settings.r2_bucket is not None
    assert settings.r2_public_base_url is not None
    return R2Uploader(
        account_id=settings.r2_account_id,
        key_id=settings.r2_access_key_id,
        secret=settings.r2_secret_access_key,
        bucket=settings.r2_bucket,
        public_base_url=settings.r2_public_base_url,
    )


# ---------------------------------------------------------------------------
# T-003: POST /api/v1/admin/chapters/{chapter_id}/video-upload-url
# ---------------------------------------------------------------------------


@router.post(
    "/chapters/{chapter_id}/video-upload-url",
    response_model=VideoUploadUrlResponse,
)
async def admin_video_upload_url(
    chapter_id: int,
    _: None = Depends(require_admin_jwt),
    settings: Settings = Depends(get_settings),
) -> VideoUploadUrlResponse:
    """Generate a presigned R2 PUT URL for direct browser video upload.

    The browser uses the returned ``upload_url`` to PUT the .mp4 directly to
    R2 (no bytes flow through the API). After the upload completes, call
    ``PUT /admin/chapters/{id}/video`` with the ``public_url`` to confirm.

    Returns 503 if R2 credentials are not configured on the server.
    """
    uploader = _build_r2_uploader(settings)
    key = f"chapters/{chapter_id}/video.mp4"
    upload_url, public_url = uploader.generate_presigned_put_url(key)
    return VideoUploadUrlResponse(
        upload_url=upload_url,
        public_url=public_url,
        key=key,
    )


# ---------------------------------------------------------------------------
# T-004: PUT /api/v1/admin/chapters/{chapter_id}/video
# ---------------------------------------------------------------------------


@router.put("/chapters/{chapter_id}/video", response_model=VideoConfirmResponse)
async def admin_confirm_video(
    chapter_id: int,
    body: VideoConfirmRequest,
    _: None = Depends(require_admin_jwt),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> VideoConfirmResponse:
    """Confirm the video URL for a chapter after a successful R2 upload.

    Saves ``video_url`` into ``chapters.manifest_json['video_url']``.

    Returns 403 if the active cycle is not in ``GENERACION`` state.
    Returns 404 if no active cycle or the chapter is not found.
    """
    cycles_repo = CyclesRepo(db)
    cycle = await cycles_repo.get_active()
    if cycle is None:
        raise ProblemDetail(
            status=404,
            code="no_active_cycle",
            title="Not Found",
            detail="No active cycle found.",
        )

    if cycle.state != "GENERACION":
        raise ProblemDetail(
            status=403,
            code="wrong_cycle_state",
            title="Forbidden",
            detail=f"Video can only be set during GENERACION. Current state: {cycle.state}",
        )

    chapters_repo = ChaptersRepo(db)
    updated = await chapters_repo.set_video_url(chapter_id, body.video_url)
    if not updated:
        raise ProblemDetail(
            status=404,
            code="chapter_not_found",
            title="Not Found",
            detail=f"Chapter {chapter_id} not found.",
        )

    await db.commit()
    return VideoConfirmResponse(chapter_id=chapter_id, video_url=body.video_url)
