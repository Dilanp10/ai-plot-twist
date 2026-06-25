"""``POST /api/v1/twists/submit`` — submit a continuation proposal.

Module 005 / Task T-007.

Authenticated endpoint that wraps :class:`TwistSubmissionService.submit`.
Validates the ``Idempotency-Key`` header, hashes the raw request body,
calls the service, and maps domain exceptions to RFC 7807 problem
responses per the contract in ``specs/005-twists-submission/contracts/
twists.yaml``.

HTTP status semantics:
  * 201 — fresh insert (``was_replay=False``)
  * 200 — idempotent replay (``was_replay=True``)
  * 422 — missing/invalid Idempotency-Key, or content out of bounds
  * 401 — missing/invalid JWT (raised by ``require_user``)
  * 403 — banned user (raised by ``require_user``)
  * 409 — window_closed | chapter_mismatch | over_quota | idempotency_conflict
  * 503 — under_maintenance | lock_busy
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import get_session_factory
from app.domain.twist_submission import (
    AlreadyFiltered,
    ChapterMismatch,
    ForbiddenNotOwner,
    IdempotencyConflict,
    InvalidCharacter,
    KillSwitchActive,
    OverQuota,
    TwistLockBusy,
    TwistNotFound,
    TwistSubmissionService,
    WindowClosed,
)
from app.domain.windows import CycleTimes
from app.infra.twists_repo import TwistWithChar
from app.infra.users_repo import UserRow
from app.logging import get_logger
from app.middleware.jwt_auth import require_user
from app.settings import Settings, get_settings

_log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/twists", tags=["twists"])

_PROBLEM_MEDIA = "application/problem+json"
_RETRY_AFTER_SECONDS = 3600


# ---------------------------------------------------------------------------
# DI helpers
# ---------------------------------------------------------------------------


def get_twist_submission_service(
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    settings: Settings = Depends(get_settings),
) -> TwistSubmissionService:
    """Build a :class:`TwistSubmissionService` per request."""
    return TwistSubmissionService(
        session_factory=factory,
        cycle_times=CycleTimes.default(),
        max_per_chapter=settings.max_twists_per_user_per_chapter,
    )


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


class _Frozen(BaseModel):
    """Common config: immutable DTOs."""

    model_config = ConfigDict(frozen=True)


class SubmitRequest(_Frozen):
    """Request body for ``POST /twists/submit``.

    ``content`` is validated by the domain normalizer (5..280 chars after
    NFKC + Cc/Cf/Co/Cs stripping + trim). The handler raises 422 on
    out-of-bounds values.

    ``character_id`` references an active row in ``characters`` (module
    013). The service raises :class:`InvalidCharacter` for unknown /
    hidden ids, mapped to 422 ``invalid_character``.
    """

    chapter_id: UUID
    content: str
    character_id: int = Field(..., ge=1)


class CharacterInTwistDTO(_Frozen):
    """Character block nested inside each item of ``GET /me/twists``."""

    id: int
    slug: str
    display_name: str
    photo_url: str


class TwistMineDTO(_Frozen):
    """Public projection of a user's own twist row."""

    public_id: UUID
    content: str
    status: str
    director_reason: str | None = None
    submitted_at: str  # ISO 8601 UTC
    deleted_at: str | None = None
    character: CharacterInTwistDTO | None = None


class SubmitResponseDTO(_Frozen):
    """``201``/``200`` response shape for ``POST /twists/submit``."""

    twist: TwistMineDTO
    remaining_submissions: int


class DeleteResponseDTO(_Frozen):
    """``200`` response shape for ``DELETE /twists/{public_id}``."""

    twist_id: UUID
    deleted_at: str  # ISO 8601 UTC
    remaining_submissions: int


# ---------------------------------------------------------------------------
# Problem helper (RFC 7807, local copy of the chapters.py pattern)
# ---------------------------------------------------------------------------


def _problem(
    *,
    request: Request,
    status: int,
    code: str,
    title: str,
    extra: dict[str, Any] | None = None,
    retry_after: int | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "code": code,
        "instance": str(request.url.path),
    }
    if extra:
        body.update(extra)
    headers: dict[str, str] = {}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(
        status_code=status,
        content=body,
        media_type=_PROBLEM_MEDIA,
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Body hash (raw — pre-normalization)
# ---------------------------------------------------------------------------


def _hash_request_body(body: SubmitRequest) -> str:
    """SHA-256 of the canonical JSON dump of the raw request body.

    Computed on the **raw** content (before NFKC normalization) so the
    idempotency key is deterministic from what the client actually sent.
    Different whitespace/zero-width chars → different hash → not an
    idempotency replay.

    ``character_id`` participates in the hash so a different character
    pick on the same key is correctly flagged as ``idempotency_conflict``.
    """
    canonical = json.dumps(
        {
            "chapter_id": str(body.chapter_id),
            "content": body.content,
            "character_id": body.character_id,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _twist_to_dto(twist: Any) -> TwistMineDTO:
    return TwistMineDTO(
        public_id=twist.public_id,
        content=twist.content,
        status=twist.status,
        director_reason=twist.director_reason,
        submitted_at=twist.submitted_at.isoformat(),
        deleted_at=twist.deleted_at.isoformat() if twist.deleted_at else None,
    )


def _twist_with_char_to_dto(item: TwistWithChar, public_base: str) -> TwistMineDTO:
    """Convert a ``TwistWithChar`` to ``TwistMineDTO`` including the character block."""
    char_dto: CharacterInTwistDTO | None = None
    if item.character is not None:
        base = public_base.rstrip("/")
        key = item.character.photo_r2_key.lstrip("/")
        char_dto = CharacterInTwistDTO(
            id=item.twist.character_id,
            slug=item.character.slug,
            display_name=item.character.display_name,
            photo_url=f"{base}/{key}",
        )
    twist = item.twist
    return TwistMineDTO(
        public_id=twist.public_id,
        content=twist.content,
        status=twist.status,
        director_reason=twist.director_reason,
        submitted_at=twist.submitted_at.isoformat(),
        deleted_at=twist.deleted_at.isoformat() if twist.deleted_at else None,
        character=char_dto,
    )


# ---------------------------------------------------------------------------
# POST /api/v1/twists/submit
# ---------------------------------------------------------------------------


@router.post(
    "/submit",
    operation_id="postTwistsSubmit",
    summary="Submit a continuation proposal for the current live chapter",
)
async def post_twists_submit(
    request: Request,
    body: SubmitRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    user: UserRow = Depends(require_user),
    service: TwistSubmissionService = Depends(get_twist_submission_service),
) -> Response:
    """Submit a twist for the currently live chapter.

    The handler delegates validation (kill-switch, window, chapter match,
    quota, idempotency) to :class:`TwistSubmissionService.submit` and
    maps each domain exception to the contracted problem response.
    """
    # 1. Idempotency-Key required (FR-001, research R-002).
    if not idempotency_key:
        _log.info(
            "twist_submit",
            outcome="error",
            user_id=int(user.id),
            code="missing_idempotency_key",
            status=422,
        )
        return _problem(
            request=request,
            status=422,
            code="missing_idempotency_key",
            title="Idempotency-Key header is required",
        )
    try:
        UUID(idempotency_key)
    except ValueError:
        _log.info(
            "twist_submit",
            outcome="error",
            user_id=int(user.id),
            code="invalid_idempotency_key",
            status=422,
        )
        return _problem(
            request=request,
            status=422,
            code="invalid_idempotency_key",
            title="Idempotency-Key must be a UUID",
        )

    body_hash = _hash_request_body(body)

    # 2. Call service.
    try:
        result = await service.submit(
            user_id=int(user.id),
            chapter_public_id=body.chapter_id,
            content=body.content,
            character_id=body.character_id,
            idempotency_key=idempotency_key,
            idempotency_body_hash=body_hash,
        )
    except KillSwitchActive as exc:
        _log.info(
            "twist_submit",
            outcome="error",
            user_id=int(user.id),
            code="under_maintenance",
            status=503,
        )
        return _problem(
            request=request,
            status=503,
            code="under_maintenance",
            title="Service is under maintenance",
            extra={
                "reason": exc.reason,
                "retry_after_seconds": _RETRY_AFTER_SECONDS,
            },
            retry_after=_RETRY_AFTER_SECONDS,
        )
    except WindowClosed:
        _log.info(
            "twist_submit",
            outcome="error",
            user_id=int(user.id),
            code="window_closed",
            status=409,
        )
        return _problem(
            request=request,
            status=409,
            code="window_closed",
            title="Submit window is closed",
        )
    except ChapterMismatch:
        _log.info(
            "twist_submit",
            outcome="error",
            user_id=int(user.id),
            code="chapter_mismatch",
            status=409,
        )
        return _problem(
            request=request,
            status=409,
            code="chapter_mismatch",
            title="chapter_id is not the currently live chapter",
        )
    except OverQuota as exc:
        _log.info(
            "twist_submit",
            outcome="error",
            user_id=int(user.id),
            code="over_quota",
            status=409,
            quota_used=exc.used,
        )
        return _problem(
            request=request,
            status=409,
            code="over_quota",
            title="Twist quota exhausted for this chapter",
            extra={"quota_used": exc.used, "quota_max": exc.max},
        )
    except IdempotencyConflict:
        _log.info(
            "twist_submit",
            outcome="error",
            user_id=int(user.id),
            code="idempotency_conflict",
            status=409,
        )
        return _problem(
            request=request,
            status=409,
            code="idempotency_conflict",
            title="Idempotency-Key was reused with a different request body",
        )
    except InvalidCharacter as exc:
        _log.info(
            "twist_submit",
            outcome="error",
            user_id=int(user.id),
            code="invalid_character",
            status=422,
            character_id=exc.character_id,
        )
        return _problem(
            request=request,
            status=422,
            code="invalid_character",
            title="character_id is unknown or hidden",
            extra={"character_id": exc.character_id},
        )
    except TwistLockBusy:
        _log.info(
            "twist_submit",
            outcome="error",
            user_id=int(user.id),
            code="lock_busy",
            status=503,
        )
        return _problem(
            request=request,
            status=503,
            code="lock_busy",
            title="Concurrent submit in progress; retry shortly",
            retry_after=1,
        )
    except ValueError as exc:
        # twist_content.normalize raises ValueError on out-of-bounds.
        _log.info(
            "twist_submit",
            outcome="error",
            user_id=int(user.id),
            code="invalid_content",
            status=422,
        )
        return _problem(
            request=request,
            status=422,
            code="invalid_content",
            title="Content is too short or too long",
            extra={"message": str(exc)},
        )

    # 3. Build success response.
    dto = SubmitResponseDTO(
        twist=_twist_to_dto(result.twist),
        remaining_submissions=result.quota.remaining,
    )
    status_code = 200 if result.was_replay else 201
    _log.info(
        "twist_submit",
        outcome="ok",
        user_id=int(user.id),
        twist_id=str(result.twist.public_id),
        chapter_id=str(result.twist.chapter_id),
        was_replay=result.was_replay,
        status=status_code,
        remaining_submissions=result.quota.remaining,
    )
    return JSONResponse(
        status_code=status_code,
        content=dto.model_dump(mode="json"),
    )


# ---------------------------------------------------------------------------
# DELETE /api/v1/twists/{public_id}
# ---------------------------------------------------------------------------


@router.delete(
    "/{public_id}",
    operation_id="deleteTwistById",
    summary="Soft-delete a pending twist (only during RECEPCION_IDEAS)",
)
async def delete_twist_by_id(
    request: Request,
    public_id: UUID,
    user: UserRow = Depends(require_user),
    service: TwistSubmissionService = Depends(get_twist_submission_service),
) -> Response:
    """Soft-delete one of the caller's twists.

    Idempotent: re-DELETE of an already-deleted twist returns the same
    200 with the original ``deleted_at``. Per FR-004, the user's quota
    is NOT freed.
    """
    try:
        result = await service.delete(
            user_id=int(user.id),
            twist_public_id=public_id,
        )
    except KillSwitchActive as exc:
        _log.info(
            "twist_delete",
            outcome="error",
            user_id=int(user.id),
            code="under_maintenance",
            status=503,
        )
        return _problem(
            request=request,
            status=503,
            code="under_maintenance",
            title="Service is under maintenance",
            extra={
                "reason": exc.reason,
                "retry_after_seconds": _RETRY_AFTER_SECONDS,
            },
            retry_after=_RETRY_AFTER_SECONDS,
        )
    except WindowClosed:
        _log.info(
            "twist_delete",
            outcome="error",
            user_id=int(user.id),
            code="window_closed",
            status=409,
        )
        return _problem(
            request=request,
            status=409,
            code="window_closed",
            title="Submit window is closed; delete no longer allowed",
        )
    except TwistNotFound:
        _log.info(
            "twist_delete",
            outcome="error",
            user_id=int(user.id),
            twist_id=str(public_id),
            code="twist_not_found",
            status=404,
        )
        return _problem(
            request=request,
            status=404,
            code="twist_not_found",
            title="Twist not found",
            extra={"public_id": str(public_id)},
        )
    except ForbiddenNotOwner:
        _log.info(
            "twist_delete",
            outcome="error",
            user_id=int(user.id),
            twist_id=str(public_id),
            code="forbidden_not_owner",
            status=403,
        )
        return _problem(
            request=request,
            status=403,
            code="forbidden_not_owner",
            title="This twist belongs to another user",
        )
    except AlreadyFiltered:
        _log.info(
            "twist_delete",
            outcome="error",
            user_id=int(user.id),
            twist_id=str(public_id),
            code="already_filtered",
            status=409,
        )
        return _problem(
            request=request,
            status=409,
            code="already_filtered",
            title="Twist has already been filtered and is immutable",
        )

    dto = DeleteResponseDTO(
        twist_id=public_id,
        deleted_at=result.deleted_at.isoformat(),
        remaining_submissions=result.quota.remaining,
    )
    _log.info(
        "twist_delete",
        outcome="ok",
        user_id=int(user.id),
        twist_id=str(public_id),
        was_idempotent=result.was_idempotent,
        status=200,
        remaining_submissions=result.quota.remaining,
    )
    return JSONResponse(
        status_code=200,
        content=dto.model_dump(mode="json"),
    )
