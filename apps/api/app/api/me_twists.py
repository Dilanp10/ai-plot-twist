"""``GET /api/v1/me/twists`` — list the caller's twists for the current chapter.

Module 005 / Task T-009.

Authenticated endpoint that wraps :class:`TwistSubmissionService.list_mine`.
Returns the user's twists for the currently live chapter plus a quota
snapshot. When there is no live chapter (e.g. pre-ESTRENO or
post-archive), returns an empty list with ``quota.used=0`` — a benign
read should not be penalized with an error.

HTTP status semantics:
  * 200 — always on success, even when empty.
  * 401 — missing/invalid JWT (raised by ``require_user``).
  * 403 — banned user (raised by ``require_user``).
  * 503 — under_maintenance when ``kill_switch.on=true``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict

from app.api.twists import (
    TwistMineDTO,
    _problem,
    _twist_to_dto,
    get_twist_submission_service,
)
from app.domain.twist_submission import (
    KillSwitchActive,
    TwistSubmissionService,
)
from app.infra.users_repo import UserRow
from app.logging import get_logger
from app.middleware.jwt_auth import require_user

_log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/me", tags=["twists"])

_RETRY_AFTER_SECONDS = 3600


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


class _Frozen(BaseModel):
    """Common config: immutable DTOs."""

    model_config = ConfigDict(frozen=True)


class QuotaDTO(_Frozen):
    """Quota snapshot for ``/me/twists``."""

    used: int
    max: int
    remaining: int


class MeTwistsResponseDTO(_Frozen):
    """``200`` response shape for ``GET /me/twists``."""

    items: list[TwistMineDTO]
    quota: QuotaDTO


# ---------------------------------------------------------------------------
# GET /api/v1/me/twists
# ---------------------------------------------------------------------------


@router.get(
    "/twists",
    operation_id="getMeTwists",
    summary="List the caller's twists for the current live chapter",
)
async def get_me_twists(
    request: Request,
    user: UserRow = Depends(require_user),
    service: TwistSubmissionService = Depends(get_twist_submission_service),
) -> Response:
    """Return the caller's twists for the active chapter.

    The handler is intentionally tolerant of "no live chapter" — that is
    a normal state (between cycles, pre-ESTRENO) and the PWA renders an
    empty panel rather than an error.
    """
    try:
        result = await service.list_mine(user_id=int(user.id))
    except KillSwitchActive as exc:
        _log.info(
            "me_twists",
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

    dto = MeTwistsResponseDTO(
        items=[_twist_to_dto(t) for t in result.items],
        quota=QuotaDTO(
            used=result.quota.used,
            max=result.quota.max,
            remaining=result.quota.remaining,
        ),
    )
    _log.info(
        "me_twists",
        outcome="ok",
        user_id=int(user.id),
        item_count=len(result.items),
        quota_used=result.quota.used,
        status=200,
    )
    payload: dict[str, Any] = dto.model_dump(mode="json")
    return JSONResponse(status_code=200, content=payload)
