"""``POST /api/v1/internal/client-log`` — best-effort client error sink.

Module 010 / Task T-012.

The PWA's :class:`ErrorBoundary` and global ``error`` /
``unhandledrejection`` handlers POST sanitized events here so we can
see what's breaking on real devices without standing up a Sentry-grade
ingestion pipeline (spec §Out of Scope).

Contract (FR-011):
  - Unauthenticated. Closed-beta, low-value target — abuse limited via
    per-IP rate-limit only.
  - 5 requests / minute / IP via the module-002 bucket. Implementation
    note: :class:`RateLimitRepo` keeps hourly buckets, so we convert
    "5/min" to "300/hour" — same average, slightly more permissive in
    bursty seconds, which is fine for a write-only log sink.
  - Maximum payload 4 KB. Above → 413.
  - Backend logs the payload as ``client_log_received`` with structured
    fields; nothing is persisted to the DB.
  - Returns 202.

Body::

    {
      "event": "boundary" | "error" | "unhandledrejection" | "csp_violation",
      "message": "TypeError: x is undefined",            # optional
      "stack": "at foo (app.js:42)",                      # optional, truncated client-side
      "route": "/today",                                  # optional
      "user_agent": "Mozilla/5.0 ...",
      "app_version": "0.1.0",
      "timestamp": "2026-06-14T14:33:00Z"
    }

Error envelopes:
  413 payload_too_large       — body bigger than 4 KB.
  429 rate_limited            — bucket exhausted; ``Retry-After`` header set.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import ProblemDetail
from app.infra.rate_limit_repo import RateLimited, RateLimitRepo
from app.logging import get_logger

_log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/internal", tags=["internal"])

# 5 events/min/IP ≈ 300/h/IP; the bucket window is hourly (module 002).
_RATE_LIMIT_PER_HOUR = 300

# FR-011: hard cap on the request body. Below the FastAPI default of 1 MB
# but well above a realistic stacktrace + UA + metadata payload.
_MAX_BODY_BYTES = 4 * 1024


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


ClientLogEvent = Literal[
    "boundary",
    "error",
    "unhandledrejection",
    "csp_violation",
]


class ClientLogPayload(BaseModel):
    """Sanitized client error payload (FR-011)."""

    event: ClientLogEvent = Field(
        ..., description="Source of the report — boundary or a global handler."
    )
    message: str | None = Field(default=None, max_length=512)
    stack: str | None = Field(default=None, max_length=2048)
    route: str | None = Field(default=None, max_length=128)
    user_agent: str = Field(..., max_length=256)
    app_version: str = Field(..., max_length=32)
    timestamp: str = Field(..., max_length=40)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_ip(request: Request) -> str:
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _retry_after_seconds() -> int:
    now = datetime.now(UTC)
    next_hour = (now + timedelta(hours=1)).replace(
        minute=0, second=0, microsecond=0
    )
    return max(1, int((next_hour - now).total_seconds()))


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post(
    "/client-log",
    operation_id="postInternalClientLog",
    summary="Sink for client-side errors (unauthenticated, IP-rate-limited)",
    status_code=202,
    response_class=Response,
)
async def post_client_log(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Accept a client error report; rate-limit per IP; never persist.

    Reads + validates the body manually so the 4 KB cap kicks in
    before pydantic decodes it (FR-011 — keeps malicious oversize
    payloads from costing CPU).
    """
    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        raise ProblemDetail(
            status=413,
            code="payload_too_large",
            title="Payload demasiado grande",
            detail=f"El cuerpo supera el límite de {_MAX_BODY_BYTES} bytes.",
        )

    # Validate BEFORE incrementing the rate-limit bucket. Malformed JSON
    # is cheap to reject (4 KB cap upstream); making it burn the per-IP
    # budget would let an attacker DoS legitimate error reports from the
    # same egress IP with a flood of garbage.
    try:
        payload = ClientLogPayload.model_validate_json(raw)
    except ValidationError as exc:
        raise ProblemDetail(
            status=422,
            code="invalid_payload",
            title="Payload inválido",
            detail=str(exc),
        ) from None

    ip = _get_ip(request)
    rate_repo = RateLimitRepo(session)
    try:
        await rate_repo.check_and_increment(
            bucket_key=f"client_log:ip:{ip}",
            max_per_window=_RATE_LIMIT_PER_HOUR,
        )
    except RateLimited:
        raise ProblemDetail(
            status=429,
            code="rate_limited",
            title="Demasiados reportes",
            detail="Probá más tarde.",
            headers={"Retry-After": str(_retry_after_seconds())},
        ) from None
    await session.commit()

    _log.info(
        "client_log_received",
        client_event=payload.event,
        message=payload.message,
        stack=payload.stack,
        route=payload.route,
        user_agent=payload.user_agent,
        app_version=payload.app_version,
        client_timestamp=payload.timestamp,
        ip=ip,
    )

    return Response(status_code=202)
