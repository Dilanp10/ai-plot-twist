"""``POST /api/v1/internal/generation/rerun`` — admin rerun of the pipeline.

Module 008 / Task T-012.

Regenerates the chapter that follows *chapter_id* (FR-013). The cycle
FSM is NOT touched — rerun never changes ``cycles.state``. When the
target cycle already has a ``next_chapter_id``, that row is deleted
first so the fresh ``INSERT`` does not collide on
``UNIQUE(season_id, day_index)``. R2 keys are content-addressed so the
old assets stay reachable until any external cleanup; documented as
acceptable per spec §Out of Scope.

Auth: ``Authorization: Bearer <ADMIN_TOKEN>`` (same middleware as
``/internal/kill-switch``).

DI: pulls :class:`Scriptwriter`, :class:`ImageProviderRouter`, and
:class:`R2Uploader` from ``request.app.state`` — wired in T-011.
Tests override the dependencies with fakes.

Body::

    {"chapter_id": "<UUID>"}            # SOURCE chapter public_id

Response 200::

    {
      "source_chapter_id": "<UUID>",
      "new_chapter_id": "<UUID>",
      "status": "ready" | "ready_degraded",
      "panels_ok": N,
      "panels_degraded": N,
      "duration_ms": T,
      "has_winner": true | false
    }

Error envelopes (RFC 7807):
  401 missing_admin_token             — Authorization header absent.
  403 bad_admin_token                 — token mismatch.
  404 chapter_not_found               — public_id has no matching chapter.
  503 generation_pipeline_unavailable — dependencies not wired (no LLM, R2, etc.).
"""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.domain.generation_pipeline import (
    GenerationSummary,
    run_generation_pipeline,
)
from app.domain.scriptwriter import Scriptwriter
from app.errors import ProblemDetail
from app.infra.r2_uploader import R2Uploader
from app.logging import get_logger
from app.middleware.admin_token import verify_admin_token
from app.providers.image import ImageProviderRouter
from app.providers.video import VideoProviderRouter
from app.settings import Settings, get_settings

_log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/internal", tags=["internal"])


# ---------------------------------------------------------------------------
# DI: pull the pipeline components off app.state (set by T-011 at startup)
# ---------------------------------------------------------------------------


def _require_state(request: Request, attr: str, kind: type) -> object:
    obj = getattr(request.app.state, attr, None)
    if obj is None or not isinstance(obj, kind):
        raise ProblemDetail(
            status=503,
            code="generation_pipeline_unavailable",
            title="Generation pipeline not configured",
            detail=(
                f"app.state.{attr} is not a {kind.__name__}; T-011 wires "
                "all generation dependencies at startup. Check the boot "
                "log for missing R2 / LLM credentials."
            ),
        )
    return obj


def get_scriptwriter(request: Request) -> Scriptwriter:
    return _require_state(request, "scriptwriter", Scriptwriter)  # type: ignore[return-value]


def get_image_router(request: Request) -> ImageProviderRouter:
    return _require_state(request, "image_router", ImageProviderRouter)  # type: ignore[return-value]


def get_r2_uploader(request: Request) -> R2Uploader:
    return _require_state(request, "r2_uploader", R2Uploader)  # type: ignore[return-value]


def get_video_router(request: Request) -> VideoProviderRouter | None:
    """Optional dependency — None when T2V isn't wired (T2I-only deployment)."""
    obj = getattr(request.app.state, "video_router", None)
    if isinstance(obj, VideoProviderRouter):
        return obj
    return None


def get_placeholder_video_url(request: Request) -> str | None:
    obj = getattr(request.app.state, "placeholder_video_url", None)
    return obj if isinstance(obj, str) else None


def get_placeholder_video_bytes(request: Request) -> bytes | None:
    obj = getattr(request.app.state, "placeholder_video_bytes", None)
    return obj if isinstance(obj, bytes) else None


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RerunPayload(BaseModel):
    """Body for ``POST /generation/rerun``."""

    chapter_id: UUID = Field(
        ...,
        description=(
            "Source chapter ``public_id`` (UUID). The pipeline regenerates "
            "the chapter that follows it."
        ),
    )


class RerunResponse(BaseModel):
    source_chapter_id: UUID
    new_chapter_id: UUID
    status: str
    panels_ok: int
    panels_degraded: int
    duration_ms: int
    has_winner: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_internal_chapter_id(
    session: AsyncSession, public_id: UUID
) -> int:
    """Return ``chapters.id`` for ``public_id`` or raise 404."""
    row = (
        await session.execute(
            sa.text("SELECT id FROM chapters WHERE public_id = :pid"),
            {"pid": str(public_id)},
        )
    ).one_or_none()
    if row is None:
        raise ProblemDetail(
            status=404,
            code="chapter_not_found",
            title="Chapter not found",
            detail=f"No chapter exists with public_id={public_id}.",
        )
    return int(row[0])


async def _delete_existing_next_chapter(
    session: AsyncSession, source_chapter_id: int
) -> None:
    """Clear ``cycles.next_chapter_id`` and delete the row it pointed to.

    Run before re-INSERT so the new chapter does not collide on
    ``UNIQUE(season_id, day_index)``. The row is deleted *after* the
    cycle column is cleared, so the FK does not block the DELETE.
    No-op when the cycle has no pending next chapter.

    Does NOT commit — the caller's pipeline owns the transaction
    boundary. If :func:`run_generation_pipeline` raises before its own
    commit (e.g. scriptwriter exhausts every LLM provider), the DELETE
    is rolled back along with everything else, leaving the cycle's
    original ``next_chapter_id`` intact.
    """
    row = (
        await session.execute(
            sa.text(
                "SELECT id, next_chapter_id FROM cycles "
                "WHERE chapter_id = :cid "
                "ORDER BY cycle_date DESC LIMIT 1"
            ),
            {"cid": source_chapter_id},
        )
    ).one_or_none()
    if row is None:
        return
    cycle_id = int(row[0])
    old_next = row[1]
    if old_next is None:
        return

    await session.execute(
        sa.text("UPDATE cycles SET next_chapter_id = NULL WHERE id = :cid"),
        {"cid": cycle_id},
    )
    await session.execute(
        sa.text("DELETE FROM chapters WHERE id = :ncid"),
        {"ncid": int(old_next)},
    )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post(
    "/generation/rerun",
    operation_id="postInternalGenerationRerun",
    summary="Re-run the generation pipeline for a chapter (admin-only)",
    response_model=RerunResponse,
    dependencies=[Depends(verify_admin_token)],
)
async def post_generation_rerun(
    payload: RerunPayload,
    db: AsyncSession = Depends(get_session),
    scriptwriter: Scriptwriter = Depends(get_scriptwriter),
    image_router: ImageProviderRouter = Depends(get_image_router),
    uploader: R2Uploader = Depends(get_r2_uploader),
    video_router: VideoProviderRouter | None = Depends(get_video_router),
    placeholder_video_url: str | None = Depends(get_placeholder_video_url),
    placeholder_video_bytes: bytes | None = Depends(get_placeholder_video_bytes),
    settings: Settings = Depends(get_settings),
) -> RerunResponse:
    """Regenerate the chapter that follows *chapter_id*.

    Does NOT touch ``cycles.state`` (FR-013). When a next chapter
    already exists for the source's cycle, its row is deleted first so
    the new INSERT lands cleanly.
    """
    internal_id = await _resolve_internal_chapter_id(db, payload.chapter_id)

    await _delete_existing_next_chapter(db, internal_id)

    assert settings.generation_placeholder_url is not None
    summary: GenerationSummary = await run_generation_pipeline(
        internal_id,
        session=db,
        scriptwriter=scriptwriter,
        image_router=image_router,
        uploader=uploader,
        placeholder_url=settings.generation_placeholder_url,
        tts_voice=settings.generation_tts_voice,
        panel_concurrency=settings.generation_panel_concurrency,
        deadline_s=settings.generation_deadline_s,
        video_router=video_router,
        placeholder_video_url=placeholder_video_url,
        placeholder_video_bytes=placeholder_video_bytes,
        clip_concurrency=settings.generation_clip_concurrency,
        clip_duration_s=settings.generation_clip_duration_s,
        video_pipeline_enabled=settings.video_pipeline_enabled,
        skip_cycle_transition=True,
    )

    _log.info(
        "generation_rerun",
        source_chapter_public_id=str(payload.chapter_id),
        source_chapter_id=internal_id,
        new_chapter_id=summary.new_chapter_id,
        new_chapter_public_id=str(summary.new_chapter_public_id),
        status=summary.status,
        panels_ok=summary.panels_ok,
        panels_degraded=summary.panels_degraded,
        duration_ms=summary.duration_ms,
        has_winner=summary.has_winner,
    )

    return RerunResponse(
        source_chapter_id=payload.chapter_id,
        new_chapter_id=summary.new_chapter_public_id,
        status=summary.status,
        panels_ok=summary.panels_ok,
        panels_degraded=summary.panels_degraded,
        duration_ms=summary.duration_ms,
        has_winner=summary.has_winner,
    )
