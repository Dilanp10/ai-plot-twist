"""``POST /api/v1/internal/kill-switch`` — admin toggle for the FSM kill-switch.

Module 003 / Task T-017.

Requires ``Authorization: Bearer <ADMIN_TOKEN>`` (T-016).

Body:
  on:     bool         — ``true`` activates the kill-switch, ``false`` deactivates
  reason: str | None   — optional human-readable reason stored in DB

Response 200:
  ``{"status": "kill_switch_active"|"kill_switch_inactive", "reason": ...}``

When ``on=true`` the executor's kill-switch check will return
``KillSwitchActive`` on the *next* cron tick.  Turning it off does NOT
auto-replay missed ticks — the operator must call ``pnpm replay-tick`` manually.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.infra.system_flags_repo import SystemFlagsRepo
from app.logging import get_logger
from app.middleware.admin_token import verify_admin_token

_log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/internal", tags=["internal"])


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------


class KillSwitchPayload(BaseModel):
    on: bool
    reason: str | None = None


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post(
    "/kill-switch",
    operation_id="postInternalKillSwitch",
    summary="Activate or deactivate the FSM kill-switch",
    dependencies=[Depends(verify_admin_token)],
)
async def post_kill_switch(
    payload: KillSwitchPayload,
    db: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Set the ``kill_switch`` system flag and return its new state.

    The change is committed immediately; the in-process cache is invalidated
    so the next executor call reads the fresh DB value.
    """
    repo = SystemFlagsRepo(db)
    await repo.set(
        key="kill_switch",
        value={"on": payload.on, "reason": payload.reason},
        updated_by="admin",
    )
    await db.commit()

    status = "kill_switch_active" if payload.on else "kill_switch_inactive"
    _log.info("kill_switch_toggled", on=payload.on, reason=payload.reason)

    return JSONResponse(
        status_code=200,
        content={"status": status, "reason": payload.reason},
    )
