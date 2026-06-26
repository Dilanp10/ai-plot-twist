"""``GET /healthz`` — liveness + database health probe.

Returns 200 with ``{"status":"ok","checks":{"database":"ok"}}`` when the DB
ping succeeds within ``DB_PING_TIMEOUT_S``; 503 with the matching error
shape otherwise. The body schema is intentionally extensible: future modules
may add keys to ``checks`` (e.g. ``"llm":"ok"``) without breaking consumers.

Per constitution Gate 9: **no exception text, stack trace, or secret ever
appears in the response body**. Failures are logged server-side with an
``outcome`` and ``duration_ms`` tag (Gate 10).
"""

from __future__ import annotations

import asyncio
import time
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import get_engine
from app.logging import get_logger

_log = get_logger(__name__)

DB_PING_TIMEOUT_S: float = 1.0

router = APIRouter(tags=["system"])


@router.get("/ping", operation_id="getPing", include_in_schema=False)
async def ping() -> dict[str, str]:
    """Lightweight liveness probe — no DB touch, used by Fly health checks."""
    return {"status": "ok"}


class HealthResponse(BaseModel):
    """Health-check payload. ``checks`` is open-ended so modules can add keys."""

    status: Literal["ok", "error"]
    checks: dict[str, Literal["ok", "error"]]


async def _ping_database(engine: AsyncEngine) -> bool:
    """Return True iff ``SELECT 1`` succeeds within the timeout.

    Any exception (connection refused, timeout, auth failure, mid-flight
    cancellation, …) is swallowed and logged with the *exception type only*
    (never its message — Gate 9). We catch :class:`asyncio.CancelledError`
    explicitly because since Python 3.8 it inherits from ``BaseException``
    and is *not* a subclass of ``Exception`` — without this branch a
    mid-connect cancel would propagate as a 500.
    """

    async def _ping() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    try:
        await asyncio.wait_for(_ping(), timeout=DB_PING_TIMEOUT_S)
    except (Exception, asyncio.CancelledError) as exc:
        _log.warning(
            "healthz_db_ping_failed",
            error_type=type(exc).__name__,
        )
        return False
    return True


@router.get(
    "/healthz",
    operation_id="getHealth",
    response_model=HealthResponse,
    summary="Liveness + DB health probe",
)
async def healthz(
    response: Response,
    engine: AsyncEngine = Depends(get_engine),
) -> HealthResponse:
    """Unauthenticated: 200 when DB is reachable, 503 otherwise."""
    start = time.monotonic()
    db_ok = await _ping_database(engine)
    duration_ms = int((time.monotonic() - start) * 1000)

    if db_ok:
        _log.info("healthz", outcome="ok", duration_ms=duration_ms)
        return HealthResponse(status="ok", checks={"database": "ok"})

    _log.warning("healthz", outcome="error", duration_ms=duration_ms)
    response.status_code = 503
    return HealthResponse(status="error", checks={"database": "error"})
