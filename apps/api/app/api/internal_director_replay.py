"""``POST /api/v1/internal/director/replay`` — admin re-run of the filter.

Module 006 / Task T-011.

Re-classifies every twist of a chapter except those with
``status='deleted_by_user'``. Reuses the orchestrator from T-009 with
``allow_already_classified=True`` so the strict ``pending_review`` guard
is dropped. The cycle FSM is NOT touched — replay never changes
``cycles.state`` (FR-014).

Auth: ``Authorization: Bearer <ADMIN_TOKEN>`` (same middleware as
``/internal/kill-switch``, module 003 T-016).

DI: the :class:`LLMProviderRouter` is read from
``request.app.state.director_router`` — T-010 wires it during FastAPI
startup. While T-010 has not landed, the endpoint returns HTTP 503
``director_router_unavailable`` so the route exists in OpenAPI without
crashing on first call.

Body::

    {"chapter_id": "<UUID>"}            # chapter public_id

Response 200::

    {
      "chapter_id": "<UUID>",
      "twist_count": N,                  # twists considered (skips deleted_by_user)
      "classified": N,                   # twists whose row actually changed
      "batches": K,
      "breakdown": {
        "approved": A,
        "rejected_offensive": O,
        "rejected_incoherent": I,
        "rejected_spam": S
      },
      "default_denied": D,               # subset of rejected_incoherent
      "slur_overrides": V,               # subset of rejected_offensive
      "duration_ms": T
    }

Error envelopes (RFC 7807):
  401 missing_admin_token             — Authorization header absent.
  403 bad_admin_token                 — token mismatch.
  404 chapter_not_found               — public_id has no matching chapter.
  503 director_router_unavailable     — router not yet registered (pre-T-010).
"""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.domain.director_filter import FilterSummary, run_director_filter
from app.errors import ProblemDetail
from app.logging import get_logger
from app.middleware.admin_token import verify_admin_token
from app.providers.llm.router import LLMProviderRouter

_log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/internal", tags=["internal"])


# ---------------------------------------------------------------------------
# DI: pull the router off app.state (set by T-010 at startup)
# ---------------------------------------------------------------------------


def get_director_router(request: Request) -> LLMProviderRouter:
    """Return the application-wide :class:`LLMProviderRouter`.

    T-010 will register the real ``[GeminiProvider, GitHubModelsProvider]``
    chain on ``app.state.director_router``. Until then, this dependency
    raises 503 so the endpoint is callable in OpenAPI but cleanly
    refuses traffic.

    Tests override this dependency with a Fake-LLM-backed router.
    """
    obj = getattr(request.app.state, "director_router", None)
    if obj is None:
        raise ProblemDetail(
            status=503,
            code="director_router_unavailable",
            title="Director router not configured",
            detail=(
                "app.state.director_router has not been set. Module 006 "
                "T-010 wires the LLMProviderRouter at startup."
            ),
        )
    if not isinstance(obj, LLMProviderRouter):
        raise ProblemDetail(
            status=503,
            code="director_router_unavailable",
            title="Director router misconfigured",
            detail=(
                "app.state.director_router exists but is not an "
                "LLMProviderRouter."
            ),
        )
    return obj


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ReplayPayload(BaseModel):
    """Body for ``POST /director/replay``."""

    chapter_id: UUID = Field(
        ...,
        description="Chapter ``public_id`` (UUID) to re-classify.",
    )


class BreakdownDTO(BaseModel):
    """Per-decision counts; subset summed equals ``twist_count``."""

    approved: int
    rejected_offensive: int
    rejected_incoherent: int
    rejected_spam: int


class ReplayResponse(BaseModel):
    chapter_id: UUID
    twist_count: int
    classified: int
    batches: int
    breakdown: BreakdownDTO
    default_denied: int
    slur_overrides: int
    duration_ms: int


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


def _to_response(
    summary: FilterSummary, chapter_public_id: UUID
) -> ReplayResponse:
    classified = (
        summary.approved
        + summary.rejected_offensive
        + summary.rejected_incoherent
        + summary.rejected_spam
    )
    return ReplayResponse(
        chapter_id=chapter_public_id,
        twist_count=summary.twist_count,
        classified=classified,
        batches=summary.batches,
        breakdown=BreakdownDTO(
            approved=summary.approved,
            rejected_offensive=summary.rejected_offensive,
            rejected_incoherent=summary.rejected_incoherent,
            rejected_spam=summary.rejected_spam,
        ),
        default_denied=summary.default_denied,
        slur_overrides=summary.slur_overrides,
        duration_ms=summary.duration_ms,
    )


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post(
    "/director/replay",
    operation_id="postInternalDirectorReplay",
    summary="Re-run the director's filter for a chapter (admin-only)",
    response_model=ReplayResponse,
    dependencies=[Depends(verify_admin_token)],
)
async def post_director_replay(
    payload: ReplayPayload,
    db: AsyncSession = Depends(get_session),
    llm_router: LLMProviderRouter = Depends(get_director_router),
) -> ReplayResponse:
    """Re-classify every non-deleted twist of *chapter_id*.

    Does NOT touch ``cycles.state``. Each batch commits inside
    :func:`run_director_filter`, so a partial failure leaves the
    earlier-batch updates persisted.
    """
    internal_id = await _resolve_internal_chapter_id(db, payload.chapter_id)

    summary = await run_director_filter(
        internal_id,
        session=db,
        router=llm_router,
        allow_already_classified=True,
    )

    _log.info(
        "director_replay",
        chapter_public_id=str(payload.chapter_id),
        chapter_id=internal_id,
        twist_count=summary.twist_count,
        approved=summary.approved,
        rejected_offensive=summary.rejected_offensive,
        rejected_incoherent=summary.rejected_incoherent,
        rejected_spam=summary.rejected_spam,
        default_denied=summary.default_denied,
        slur_overrides=summary.slur_overrides,
        duration_ms=summary.duration_ms,
    )

    return _to_response(summary, payload.chapter_id)
