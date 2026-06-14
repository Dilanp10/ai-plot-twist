"""HTTP endpoints for module 007 — voting.

Module 007 / Tasks T-006 (GET /twists/vote-feed) + T-007 (POST /twists/vote).

Authenticated endpoints that wrap :class:`VoteService`. The HTTP layer
maps domain exceptions to RFC 7807 problem responses per the contract in
``specs/007-voting/contracts/voting.yaml``.

Status semantics:
  * 200 — feed page (GET) or fresh / idempotent vote (POST)
  * 401 — missing/invalid JWT (raised by ``require_user``)
  * 403 — banned user (raised by ``require_user``)
  * 409 — window_closed | over_quota | already_voted | twist_not_votable |
          chapter_mismatch | cannot_self_vote
  * 422 — cursor_invalid | bad params
  * 503 — under_maintenance | lock_busy
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import get_session_factory
from app.domain.vote_cursor import CursorInvalid
from app.domain.vote_service import (
    AlreadyVoted,
    CannotSelfVote,
    ChapterMismatch,
    KillSwitchActive,
    OverQuota,
    TwistNotVotable,
    VoteLockBusy,
    VoteService,
    WindowClosed,
)
from app.domain.windows import CycleTimes
from app.infra.users_repo import UserRow
from app.logging import get_logger
from app.middleware.jwt_auth import require_user
from app.settings import Settings, get_settings

_log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/twists", tags=["voting"])

_PROBLEM_MEDIA = "application/problem+json"
_RETRY_AFTER_SECONDS = 3600


# ---------------------------------------------------------------------------
# DI helpers
# ---------------------------------------------------------------------------


def get_vote_service(
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
    settings: Settings = Depends(get_settings),
) -> VoteService:
    """Build a :class:`VoteService` per request."""
    return VoteService(
        session_factory=factory,
        cycle_times=CycleTimes.default(),
        max_per_chapter=settings.max_votes_per_user_per_chapter,
        allow_self_vote=settings.allow_self_vote,
    )


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class FeedItemDTO(_Frozen):
    """One row in :class:`VoteFeedResponseDTO.items`."""

    id: UUID
    content: str
    vote_count: int
    has_my_vote: bool


class PageDTO(_Frozen):
    next_cursor: str | None
    limit: int
    total_approved: int


class QuotaDTO(_Frozen):
    used: int
    max: int
    remaining: int


class VoteFeedResponseDTO(_Frozen):
    items: list[FeedItemDTO]
    page: PageDTO
    user_quota: QuotaDTO


class VoteRequest(_Frozen):
    twist_id: UUID = Field(..., description="UUID of the twist to vote for.")


class VoteResponseDTO(_Frozen):
    twist_id: UUID
    new_vote_count: int
    user_quota: QuotaDTO


# ---------------------------------------------------------------------------
# Problem helper
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


def _quota_dto(used: int, max_: int) -> QuotaDTO:
    return QuotaDTO(used=used, max=max_, remaining=max(0, max_ - used))


# ---------------------------------------------------------------------------
# GET /api/v1/twists/vote-feed
# ---------------------------------------------------------------------------


@router.get(
    "/vote-feed",
    operation_id="getTwistsVoteFeed",
    summary="Approved twists for the current live chapter, paginated",
)
async def get_twists_vote_feed(
    request: Request,
    sort: str = Query(default="random", pattern="^(random|recent|hot)$"),
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = Query(default=None),
    user: UserRow = Depends(require_user),
    service: VoteService = Depends(get_vote_service),
) -> Response:
    try:
        result = await service.feed(
            user_id=int(user.id),
            sort=sort,
            limit=limit,
            cursor=cursor,
        )
    except KillSwitchActive as exc:
        _log.info(
            "vote_feed",
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
            "vote_feed",
            outcome="error",
            user_id=int(user.id),
            code="window_closed",
            status=409,
        )
        return _problem(
            request=request,
            status=409,
            code="window_closed",
            title="Vote window is closed",
        )
    except CursorInvalid as exc:
        _log.info(
            "vote_feed",
            outcome="error",
            user_id=int(user.id),
            code="cursor_invalid",
            status=422,
        )
        return _problem(
            request=request,
            status=422,
            code="cursor_invalid",
            title="Cursor is malformed or does not match the requested sort",
            extra={"message": str(exc)},
        )

    dto = VoteFeedResponseDTO(
        items=[
            FeedItemDTO(
                id=item.public_id,
                content=item.content,
                vote_count=item.vote_count,
                has_my_vote=item.has_my_vote,
            )
            for item in result.items
        ],
        page=PageDTO(
            next_cursor=result.page.next_cursor,
            limit=result.page.limit,
            total_approved=result.page.total_approved,
        ),
        user_quota=_quota_dto(result.quota.used, result.quota.max),
    )
    _log.info(
        "vote_feed",
        outcome="ok",
        user_id=int(user.id),
        sort=sort,
        limit=limit,
        items_returned=len(result.items),
        total_approved=result.page.total_approved,
        status=200,
    )
    return JSONResponse(status_code=200, content=dto.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# POST /api/v1/twists/vote
# ---------------------------------------------------------------------------


@router.post(
    "/vote",
    operation_id="postTwistsVote",
    summary="Cast a single vote for an approved twist",
)
async def post_twists_vote(
    request: Request,
    body: VoteRequest,
    user: UserRow = Depends(require_user),
    service: VoteService = Depends(get_vote_service),
) -> Response:
    try:
        result = await service.cast(
            user_id=int(user.id),
            twist_public_id=body.twist_id,
        )
    except KillSwitchActive as exc:
        _log.info(
            "vote_cast",
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
            "vote_cast",
            outcome="error",
            user_id=int(user.id),
            code="window_closed",
            status=409,
        )
        return _problem(
            request=request,
            status=409,
            code="window_closed",
            title="Vote window is closed",
        )
    except TwistNotVotable:
        _log.info(
            "vote_cast",
            outcome="error",
            user_id=int(user.id),
            twist_id=str(body.twist_id),
            code="twist_not_votable",
            status=409,
        )
        return _problem(
            request=request,
            status=409,
            code="twist_not_votable",
            title="Twist does not exist or is not approved",
        )
    except ChapterMismatch:
        _log.info(
            "vote_cast",
            outcome="error",
            user_id=int(user.id),
            twist_id=str(body.twist_id),
            code="chapter_mismatch",
            status=409,
        )
        return _problem(
            request=request,
            status=409,
            code="chapter_mismatch",
            title="Twist belongs to a different chapter",
        )
    except CannotSelfVote:
        _log.info(
            "vote_cast",
            outcome="error",
            user_id=int(user.id),
            twist_id=str(body.twist_id),
            code="cannot_self_vote",
            status=409,
        )
        return _problem(
            request=request,
            status=409,
            code="cannot_self_vote",
            title="Self-voting is disabled",
        )
    except OverQuota as exc:
        _log.info(
            "vote_cast",
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
            title="Vote quota exhausted for this chapter",
            extra={"quota_used": exc.used, "quota_max": exc.max},
        )
    except AlreadyVoted:
        _log.info(
            "vote_cast",
            outcome="error",
            user_id=int(user.id),
            twist_id=str(body.twist_id),
            code="already_voted",
            status=409,
        )
        return _problem(
            request=request,
            status=409,
            code="already_voted",
            title="You already voted for this twist",
            extra={"twist_id": str(body.twist_id)},
        )
    except VoteLockBusy:
        _log.info(
            "vote_cast",
            outcome="error",
            user_id=int(user.id),
            code="lock_busy",
            status=503,
        )
        return _problem(
            request=request,
            status=503,
            code="lock_busy",
            title="Concurrent vote in progress; retry shortly",
            retry_after=1,
        )

    dto = VoteResponseDTO(
        twist_id=result.twist_public_id,
        new_vote_count=result.new_vote_count,
        user_quota=_quota_dto(result.quota.used, result.quota.max),
    )
    _log.info(
        "vote_cast",
        outcome="ok",
        user_id=int(user.id),
        twist_id=str(result.twist_public_id),
        new_vote_count=result.new_vote_count,
        quota_used=result.quota.used,
        status=200,
    )
    return JSONResponse(status_code=200, content=dto.model_dump(mode="json"))
