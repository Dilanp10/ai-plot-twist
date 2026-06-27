"""``POST /api/v1/internal/transition`` — HMAC-signed FSM transition trigger.

Module 003 / Task T-015.

Replaced the module-001 stub (in-process replay cache + noop) with the real
FSM transition orchestration:

  1. HMAC + timestamp-drift verification (unchanged dependency).
  2. Pydantic semantic validation.
  3. Dispatch:
     - ``to == "WATCHDOG"``  →  ``watchdog.check(db, now_utc)``
     - anything else         →  ``cycle_executor.transition(db, to, ...)``
  4. Domain exception → RFC 7807 mapping.
  5. ``BackgroundTask`` spawn if ``result.side_effect_name`` is set.

X-Dev-Skip-Dwell header:
  Passing ``X-Dev-Skip-Dwell: 1`` bypasses the FSM min-dwell time fence.
  This is **only** honoured when ``settings.env != "prod"`` (i.e. for the
  ``pnpm replay-tick --no-dwell-check`` dev workflow, T-020).

Response codes:
  202  applied              — transition committed; side effect may be spawned.
  200  already_applied      — idempotent replay; DB already has this row.
  200  kill_switch_active   — kill switch is on; tick silently no-ops.
  200  watchdog_*           — watchdog verdict (healthy or forced-FAILED).
  409  illegal_transition   — (from, to) pair is not a legal FSM edge.
  409  time_fence_violation — min-dwell guard triggered.
  503  no_active_season     — no active season / cycle exists.
  503  lock_busy            — advisory lock not acquired within 2 s.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session, get_session_factory
from app.email.resend_client import send_generation_email
from app.domain.cycle_executor import (
    IllegalTransition,
    KillSwitchActive,
    LockBusy,
    NoActiveCycle,
    TimeFenceViolation,
    TransitionResult,
)
from app.domain.cycle_executor import (
    transition as executor_transition,
)
from app.domain.safe_side_effect import run_safe
from app.domain.watchdog import check as watchdog_check
from app.errors import ProblemDetail
from app.logging import get_logger
from app.middleware.hmac_tick import verify_hmac_tick
from app.settings import Settings, get_settings

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

# All FSM target states + the special WATCHDOG pseudo-state.
_VALID_TARGETS = Literal[
    "PENDING_RELEASE",
    "ESTRENO",
    "RECEPCION_IDEAS",
    "FILTERING",
    "VOTACION",
    "GENERACION",
    "FAILED",
    "WATCHDOG",
]


class TickPayload(BaseModel):
    """Validated tick payload (semantic layer on top of the HMAC dependency)."""

    to: _VALID_TARGETS
    ts: int
    trigger_id: str = Field(min_length=4, max_length=128)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/internal", tags=["internal"])


def _summarize_validation_errors(exc: ValidationError) -> str:
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        parts.append(f"{loc}: {err['type']}")
    return "; ".join(parts)


@router.post(
    "/transition",
    operation_id="postInternalTransition",
    summary="HMAC-signed FSM state-transition trigger",
)
async def post_transition(
    background_tasks: BackgroundTasks,
    request_headers_for_skip_dwell: str | None = Header(
        default=None, alias="X-Dev-Skip-Dwell"
    ),
    raw_payload: dict[str, Any] = Depends(verify_hmac_tick),
    db: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """Validate semantic payload, dispatch to watchdog or FSM executor.

    Commits the DB session on success; the session is auto-rolled-back by the
    DI context manager if a domain exception escapes as a ``ProblemDetail``.
    """
    now_utc = datetime.now(UTC)

    # ── 1. Semantic validation ────────────────────────────────────────────
    try:
        payload = TickPayload.model_validate(raw_payload)
    except ValidationError as exc:
        raise ProblemDetail(
            status=422,
            code="bad_payload",
            title="Invalid payload",
            detail=_summarize_validation_errors(exc),
        ) from exc

    # ── 2. skip_dwell (dev/test only) ────────────────────────────────────
    skip_dwell = (
        request_headers_for_skip_dwell == "1"
        and settings.env != "prod"
    )

    # ── 3. Dispatch ───────────────────────────────────────────────────────
    if payload.to == "WATCHDOG":
        return await _handle_watchdog(
            db=db,
            now_utc=now_utc,
            discord_webhook_url=settings.discord_webhook_url,
        )

    return await _handle_transition(
        db=db,
        payload=payload,
        now_utc=now_utc,
        skip_dwell=skip_dwell,
        settings=settings,
        background_tasks=background_tasks,
    )


# ---------------------------------------------------------------------------
# Dispatch helpers
# ---------------------------------------------------------------------------


async def _handle_watchdog(
    db: AsyncSession,
    now_utc: datetime,
    discord_webhook_url: str | None,
) -> JSONResponse:
    """Run watchdog check and return a 200 verdict response."""
    result = await watchdog_check(db, now_utc, discord_webhook_url)
    _log.info(
        "watchdog_tick",
        verdict=result.verdict,
        cycle_id=result.cycle_id,
        forced_failed=result.forced_failed,
    )
    return JSONResponse(
        status_code=200,
        content={
            "status": "watchdog_ok",
            "verdict": result.verdict,
            "cycle_id": result.cycle_id,
            "cycle_state": result.cycle_state,
            "elapsed_seconds": result.elapsed_seconds,
            "forced_failed": result.forced_failed,
        },
    )


async def _handle_transition(
    db: AsyncSession,
    payload: TickPayload,
    now_utc: datetime,
    skip_dwell: bool,
    settings: Settings,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """Run the FSM executor and map the result to an HTTP response."""
    try:
        result: TransitionResult = await executor_transition(
            session=db,
            requested_to=payload.to,
            triggered_by="cron",
            trigger_id=payload.trigger_id,
            skip_dwell=skip_dwell,
        )
    except KillSwitchActive as exc:
        _log.info(
            "tick_kill_switch_active",
            to=payload.to,
            trigger_id=payload.trigger_id,
            reason=exc.reason,
        )
        return JSONResponse(
            status_code=200,
            content={
                "status": "kill_switch_active",
                "reason": exc.reason,
            },
        )
    except NoActiveCycle as exc:
        raise ProblemDetail(
            status=503,
            code="no_active_season",
            title="No active season",
            detail=str(exc),
        ) from exc
    except LockBusy as exc:
        raise ProblemDetail(
            status=503,
            code="lock_busy",
            title="Cycle lock busy",
            detail=f"Advisory lock for cycle {exc.cycle_id} could not be acquired.",
        ) from exc
    except IllegalTransition as exc:
        raise ProblemDetail(
            status=409,
            code="illegal_transition",
            title="Illegal FSM transition",
            detail=f"{exc.from_state!r} → {exc.to_state!r} is not a valid edge.",
        ) from exc
    except TimeFenceViolation as exc:
        raise ProblemDetail(
            status=409,
            code="time_fence_violation",
            title="Min-dwell guard triggered",
            detail=(
                f"{exc.from_state!r} → {exc.to_state!r} requires "
                f"{exc.min_dwell_s} s dwell; {exc.elapsed_s:.1f} s elapsed. "
                f"Earliest valid: {exc.earliest_at.isoformat()}"
            ),
        ) from exc

    # ── Already-applied (idempotent replay) ──────────────────────────────
    if result.status == "already_applied":
        _log.info(
            "tick_already_applied",
            to=payload.to,
            trigger_id=payload.trigger_id,
            applied_at=result.applied_at.isoformat(),
        )
        return JSONResponse(
            status_code=200,
            content={
                "status": "already_applied",
                "applied_at": result.applied_at.isoformat(),
            },
        )

    # ── Applied — spawn side effect if needed ─────────────────────────────
    side_effect_spawned: str | None = None
    if result.side_effect_name is not None:
        side_effect_spawned = result.side_effect_name
        background_tasks.add_task(
            run_safe,
            name=result.side_effect_name,
            chapter_id=result.chapter_id,
            cycle_id=result.cycle_id,
            session_factory=get_session_factory(),
            discord_webhook_url=settings.discord_webhook_url,
        )

    # ── Generation notification email (best-effort, never blocks FSM) ─────
    if payload.to == "GENERACION":
        background_tasks.add_task(
            send_generation_email,
            session_factory=get_session_factory(),
            chapter_id=result.chapter_id,
            resend_api_key=settings.resend_api_key,
            admin_email=settings.admin_email,
            r2_public_base_url=settings.r2_public_base_url,
        )

    _log.info(
        "tick_applied",
        to=payload.to,
        trigger_id=payload.trigger_id,
        transition_id=result.transition_id,
        side_effect=side_effect_spawned,
    )

    return JSONResponse(
        status_code=202,
        content={
            "status": "applied",
            "transition_id": result.transition_id,
            "applied_at": result.applied_at.isoformat(),
            "side_effect_spawned": side_effect_spawned,
        },
    )
