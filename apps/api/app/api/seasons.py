"""``GET /api/v1/seasons/{slug}`` — season meta + redacted public bible.

Module 004 / Task T-009.

Maps :class:`ContentService.season` results into HTTP responses per spec
FR-005/FR-008 and RFC 7807 problem responses per research R-007.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.domain.content_service import (
    ContentService,
    KillSwitchActive,
    NoActiveSeason,
    SeasonChaptersDTO,
    SeasonNotFound,
    SeasonResponseDTO,
)
from app.domain.windows import CycleTimes
from app.infra.content_repo import ContentRepo
from app.infra.system_flags_repo import SystemFlagsRepo
from app.logging import get_logger
from app.middleware.cache_headers import set_cache

_log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/seasons", tags=["seasons"])

_RETRY_AFTER_SECONDS = 3600
_PROBLEM_MEDIA = "application/problem+json"


def _get_content_service(db: AsyncSession = Depends(get_session)) -> ContentService:
    return ContentService(
        content_repo=ContentRepo(db),
        flags_repo=SystemFlagsRepo(db),
        cycle_times=CycleTimes.default(),
    )


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
    response = JSONResponse(
        status_code=status, content=body, media_type=_PROBLEM_MEDIA, headers=headers
    )
    set_cache(response, max_age=0, no_store=True)
    return response


@router.get(
    "/current/chapters",
    operation_id="getCurrentSeasonChapters",
    summary="All released chapters for the active season (series album view)",
    response_model=SeasonChaptersDTO,
)
async def get_current_season_chapters(
    request: Request,
    service: ContentService = Depends(_get_content_service),
) -> Response:
    """Resolve ``GET /seasons/current/chapters``.

    Responses:
      * 200 — :class:`SeasonChaptersDTO` with all live/archived chapters.
      * 503 — ``under_maintenance`` or ``no_active_season``.
    """
    try:
        dto = await service.chapters_list()
    except KillSwitchActive as exc:
        _log.info(
            "content_read",
            endpoint="season_chapters",
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
            retry_after=_RETRY_AFTER_SECONDS,
        )
    except NoActiveSeason:
        _log.info(
            "content_read",
            endpoint="season_chapters",
            status=503,
            cache_hint="no-store",
            code="no_active_season",
        )
        return _problem(
            request=request,
            status=503,
            code="no_active_season",
            title="No active season",
        )

    _log.info(
        "content_read",
        endpoint="season_chapters",
        status=200,
        cache_hint="miss",
        chapter_count=len(dto.chapters),
    )
    payload = JSONResponse(status_code=200, content=dto.model_dump(mode="json"))
    set_cache(payload, max_age=60, swr=300)
    return payload


@router.get(
    "/{slug}",
    operation_id="getSeasonBySlug",
    summary="Season meta + public-safe bible + chapter counts",
    response_model=SeasonResponseDTO,
)
async def get_season_by_slug(
    request: Request,
    slug: str,
    service: ContentService = Depends(_get_content_service),
) -> Response:
    """Resolve ``GET /seasons/{slug}`` per spec FR-005/FR-008.

    Responses:
      * 200 — :class:`SeasonResponseDTO` with bible filtered to PUBLIC_BIBLE_KEYS.
      * 503 — ``under_maintenance``.
      * 404 — ``season_not_found``.
    """
    try:
        dto = await service.season(slug)
    except KillSwitchActive as exc:
        _log.info(
            "content_read",
            endpoint="season",
            status=503,
            cache_hint="no-store",
            code="under_maintenance",
            season_slug=slug,
        )
        return _problem(
            request=request,
            status=503,
            code="under_maintenance",
            title="Service is under maintenance",
            extra={"reason": exc.reason, "retry_after_seconds": _RETRY_AFTER_SECONDS},
            retry_after=_RETRY_AFTER_SECONDS,
        )
    except SeasonNotFound:
        _log.info(
            "content_read",
            endpoint="season",
            status=404,
            cache_hint="no-store",
            code="season_not_found",
            season_slug=slug,
        )
        return _problem(
            request=request,
            status=404,
            code="season_not_found",
            title="Season not found",
            extra={"slug": slug},
        )

    _log.info(
        "content_read",
        endpoint="season",
        status=200,
        cache_hint="miss",
        season_slug=slug,
    )
    payload = JSONResponse(status_code=200, content=dto.model_dump(mode="json"))
    set_cache(payload, max_age=300, swr=3600)
    return payload
