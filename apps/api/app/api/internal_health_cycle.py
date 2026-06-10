"""``GET /api/v1/internal/health/cycle`` — cycle FSM health snapshot.

Module 003 / Task T-018.

Returns (FR-007):
  - current FSM state + time in state
  - last 5 state transitions
  - kill-switch status
  - next 4 cron ticks (all within the next 24 h)

No authentication required — this is a read-only diagnostic endpoint.

Response 200 always:
  ``cycle_id`` is null when no active cycle exists.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.domain.cycle_clock import next_n_ticks
from app.infra.cycles_repo import CyclesRepo
from app.infra.system_flags_repo import SystemFlagsRepo
from app.infra.transitions_repo import TransitionsRepo
from app.logging import get_logger

_log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/internal", tags=["internal"])


@router.get(
    "/health/cycle",
    operation_id="getInternalHealthCycle",
    summary="FSM cycle health snapshot",
)
async def get_health_cycle(
    db: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Return a point-in-time snapshot of the active cycle's FSM state."""
    now_utc = datetime.now(UTC)

    # ── Kill-switch status ────────────────────────────────────────────────
    flag = await SystemFlagsRepo(db).get("kill_switch")
    kill_switch: dict[str, Any] = {
        "on": bool(flag.flag_value.get("on", False)) if flag else False,
        "reason": flag.flag_value.get("reason") if flag else None,
    }

    # ── Next 4 cron ticks (always within ~24 h) ───────────────────────────
    next_ticks = [
        {
            "tick": instance.slot.tick,
            "fires_at_utc": instance.fires_at_utc.isoformat(),
        }
        for instance in next_n_ticks(now_utc, 4)
    ]

    # ── Active cycle ──────────────────────────────────────────────────────
    cycle = await CyclesRepo(db).get_active()

    if cycle is None:
        _log.info("health_cycle_no_active", kill_switch_on=kill_switch["on"])
        return JSONResponse(
            status_code=200,
            content={
                "cycle_id": None,
                "chapter_id": None,
                "season_id": None,
                "current_state": None,
                "state_entered_at": None,
                "elapsed_seconds": None,
                "kill_switch": kill_switch,
                "last_transitions": [],
                "next_ticks": next_ticks,
            },
        )

    # ── Last 5 transitions ────────────────────────────────────────────────
    transitions = await TransitionsRepo(db).get_recent(cycle.id, limit=5)

    # state_entered_at from DB may be naive (UTC); normalise to tz-aware.
    entered_at = cycle.state_entered_at
    if entered_at.tzinfo is None:
        entered_at = entered_at.replace(tzinfo=UTC)
    elapsed_seconds = (now_utc - entered_at).total_seconds()

    _log.info(
        "health_cycle_ok",
        cycle_id=cycle.id,
        state=cycle.state,
        elapsed_s=round(elapsed_seconds, 1),
        kill_switch_on=kill_switch["on"],
    )

    return JSONResponse(
        status_code=200,
        content={
            "cycle_id": cycle.id,
            "chapter_id": cycle.chapter_id,
            "season_id": cycle.season_id,
            "current_state": cycle.state,
            "state_entered_at": entered_at.isoformat(),
            "elapsed_seconds": round(elapsed_seconds, 3),
            "kill_switch": kill_switch,
            "last_transitions": [
                {
                    "id": t.id,
                    "from_state": t.from_state,
                    "to_state": t.to_state,
                    "triggered_by": t.triggered_by,
                    "trigger_id": t.trigger_id,
                    "created_at": (
                        t.created_at.replace(tzinfo=UTC).isoformat()
                        if t.created_at.tzinfo is None
                        else t.created_at.isoformat()
                    ),
                }
                for t in transitions
            ],
            "next_ticks": next_ticks,
        },
    )
