"""``GET /api/v1/chapters/today`` — today's chapter + cycle state + windows.

Module 004 / Task T-007.

Maps :class:`ContentService.today` results into HTTP responses per spec FR-001/
FR-002 and RFC 7807 problem responses per research R-007. ``GET /chapters/
{public_id}`` is added in T-008 to the same router.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.domain.content_service import (
    ContentService,
    KillSwitchActive,
    NoActiveSeason,
    NoLiveChapter,
    TodayResponseDTO,
)
from app.domain.etag import derive_etag
from app.domain.windows import CycleTimes
from app.infra.content_repo import ContentRepo
from app.infra.system_flags_repo import SystemFlagsRepo
from app.logging import get_logger
from app.middleware.cache_headers import set_cache, set_etag

_log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/chapters", tags=["chapters"])

# Per spec edge case: under_maintenance suggests the client back off 1 h.
_RETRY_AFTER_SECONDS = 3600
_PROBLEM_MEDIA = "application/problem+json"


# ---------------------------------------------------------------------------
# DI helper
# ---------------------------------------------------------------------------


def get_content_service(db: AsyncSession = Depends(get_session)) -> ContentService:
    """Build a :class:`ContentService` bound to the request's DB session."""
    return ContentService(
        content_repo=ContentRepo(db),
        flags_repo=SystemFlagsRepo(db),
        cycle_times=CycleTimes.default(),
    )


# ---------------------------------------------------------------------------
# Problem-response helpers (RFC 7807, with extra fields per R-007)
# ---------------------------------------------------------------------------


def _problem(
    *,
    request: Request,
    status: int,
    code: str,
    title: str,
    extra: dict[str, Any] | None = None,
    cache_no_store: bool = False,
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
    response = JSONResponse(
        status_code=status, content=body, media_type=_PROBLEM_MEDIA, headers=headers
    )
    if cache_no_store:
        set_cache(response, max_age=0, no_store=True)
    return response


def _matches_etag(if_none_match: str, current_hex: str) -> bool:
    """Strip quotes and compare. The header is the quoted form per RFC 7232."""
    candidate = if_none_match.strip().strip('"')
    return candidate == current_hex


# ---------------------------------------------------------------------------
# GET /api/v1/chapters/today
# ---------------------------------------------------------------------------


@router.get(
    "/today",
    operation_id="getChaptersToday",
    summary="Today's chapter + cycle state + window timestamps",
    response_model=TodayResponseDTO,
)
async def get_chapters_today(
    request: Request,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    service: ContentService = Depends(get_content_service),
) -> Response:
    """Resolve ``GET /chapters/today`` per spec FR-001/FR-002.

    Responses:
      * 200 — :class:`TodayResponseDTO` + ETag + short-fresh + swr + must-revalidate.
      * 304 — empty body, when ``If-None-Match`` matches the current ETag.
      * 503 — ``under_maintenance`` (kill-switch on) or ``no_active_season``.
      * 404 — ``no_live_chapter`` with ``first_release_at``.
    """
    try:
        dto = await service.today()
    except KillSwitchActive as exc:
        _log.info(
            "content_read",
            endpoint="today",
            status=503,
            cache_hint="no-store",
            code="under_maintenance",
        )
        return _problem(
            request=request,
            status=503,
            code="under_maintenance",
            title="Service is under maintenance",
            extra={"reason": exc.reason, "retry_after_seconds": _RETRY_AFTER_SECONDS},
            cache_no_store=True,
            retry_after=_RETRY_AFTER_SECONDS,
        )
    except NoActiveSeason:
        _log.info(
            "content_read",
            endpoint="today",
            status=503,
            cache_hint="no-store",
            code="no_active_season",
        )
        return _problem(
            request=request,
            status=503,
            code="no_active_season",
            title="No active season",
            cache_no_store=True,
        )
    except NoLiveChapter as exc:
        _log.info(
            "content_read",
            endpoint="today",
            status=404,
            cache_hint="no-store",
            code="no_live_chapter",
        )
        return _problem(
            request=request,
            status=404,
            code="no_live_chapter",
            title="No chapter has been released yet",
            extra={"first_release_at": exc.first_release_at.isoformat()},
            cache_no_store=True,
        )

    etag = derive_etag(dto.chapter.id, dto.cycle_state, dto.chapter.released_at)

    if if_none_match is not None and _matches_etag(if_none_match, etag):
        _log.info(
            "content_read",
            endpoint="today",
            status=304,
            cache_hint="hit",
            chapter_id=str(dto.chapter.id),
        )
        not_modified = Response(status_code=304)
        set_etag(not_modified, etag)
        set_cache(not_modified, max_age=60, swr=600, must_revalidate=True)
        return not_modified

    _log.info(
        "content_read",
        endpoint="today",
        status=200,
        cache_hint="miss",
        chapter_id=str(dto.chapter.id),
        cycle_state=dto.cycle_state,
    )
    payload = JSONResponse(status_code=200, content=dto.model_dump(mode="json"))
    set_etag(payload, etag)
    set_cache(payload, max_age=60, swr=600, must_revalidate=True)
    return payload
