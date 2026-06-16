"""Cycle state-transition executor.

Module 003 / Task T-012.

Orchestrates the full FSM transition in a single DB transaction:

  1. Kill-switch check (cached, 30 s TTL)
  2. Get active cycle
  3. pg_advisory_xact_lock on cycle.id (2 s timeout → LockBusy)
  4. Re-read cycle after holding lock (authoritative state)
  5. Idempotency: if trigger_id already applied → already_applied (200)
  6. FSM compute (raises IllegalTransition / TimeFenceViolation)
  7. Insert state_transitions row (ON CONFLICT DO NOTHING safety net)
  8. Update cycle.state
  9. Apply chapter side effects from TransitionPlan.state_updates
  10. COMMIT → advisory lock released
  11. Return TransitionResult

Side effects are NOT spawned here.  The caller (HTTP handler) reads
``result.side_effect_name`` and creates a FastAPI ``BackgroundTask``
using ``app.domain.side_effects.get(result.side_effect_name)``.

Exceptions the HTTP handler should map to HTTP codes:
  KillSwitchActive   → 200  {"status": "kill_switch_active", "reason": "…"}
  NoActiveCycle      → 503  {"code": "no_active_season"}
  LockBusy           → 503  {"code": "lock_busy"}
  IllegalTransition  → 409  {"code": "illegal_transition", …}
  TimeFenceViolation → 409  {"code": "time_fence_violation", …}
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cycle_fsm import (
    IllegalTransition,
    TimeFenceViolation,
    TransitionPlan,
    compute,
)
from app.infra.chapters_repo import ChaptersRepo
from app.infra.cycles_repo import CyclesRepo, LockBusy
from app.infra.system_flags_repo import SystemFlagsRepo
from app.infra.transitions_repo import TransitionsRepo

__all__ = [
    # Re-exported so callers only need one import for all executor exceptions:
    "IllegalTransition",
    "KillSwitchActive",
    "LockBusy",
    "NoActiveCycle",
    "TimeFenceViolation",
    "TransitionResult",
    "transition",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result + domain exceptions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransitionResult:
    """Outcome of a completed (or idempotent) ``transition()`` call."""

    status: Literal["applied", "already_applied"]
    transition_id: int | None   # id of the state_transitions row; None = already_applied
    applied_at: datetime        # created_at of the original/new transition row
    side_effect_name: str | None  # DI registry key to spawn (None = nothing to spawn)
    cycle_id: int
    chapter_id: int


class KillSwitchActive(Exception):
    """Kill-switch flag is on — all tick transitions are no-ops."""

    def __init__(self, reason: str | None) -> None:
        self.reason = reason
        super().__init__(f"Kill switch is active: {reason!r}")


class NoActiveCycle(Exception):
    """No active season or cycle exists; bootstrap required."""


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


async def transition(
    session: AsyncSession,
    requested_to: str,
    triggered_by: str,
    trigger_id: str | None,
    payload_json: dict[str, Any] | None = None,
    *,
    skip_dwell: bool = False,
) -> TransitionResult:
    """Execute a cycle state transition end-to-end.

    Commits *session* on success.  On any exception the caller must rollback.

    Parameters
    ----------
    session:
        Active ``AsyncSession`` from the FastAPI DI container.
    requested_to:
        Target FSM state (e.g. ``"ESTRENO"``).
    triggered_by:
        Vocabulary: ``cron`` | ``admin`` | ``retry`` | ``side_effect``
        | ``watchdog``.
    trigger_id:
        Opaque idempotency key (GitHub run id, replay UUID, …).
        Pass *None* for internally-triggered transitions (side effects).
    payload_json:
        Optional metadata stored in ``state_transitions.payload_json``.
    skip_dwell:
        Bypass the FSM min-dwell time fence.  Only for admin / dev paths;
        never set on the cron hot path.

    Returns
    -------
    TransitionResult

    Raises
    ------
    KillSwitchActive, NoActiveCycle, LockBusy,
    IllegalTransition, TimeFenceViolation
    """
    start = time.monotonic()
    now_utc = datetime.now(UTC)

    flags_repo = SystemFlagsRepo(session)
    cycles_repo = CyclesRepo(session)
    tr_repo = TransitionsRepo(session)
    ch_repo = ChaptersRepo(session)

    # Step 1 — Kill-switch check (cheap: 30 s in-process cache).
    flag = await flags_repo.get("kill_switch")
    if flag is not None and flag.flag_value.get("on") is True:
        raise KillSwitchActive(reason=flag.flag_value.get("reason"))

    # Step 2 — Get active cycle for the advisory lock key.
    cycle = await cycles_repo.get_active()
    if cycle is None:
        raise NoActiveCycle("No active season/cycle — run pnpm bootstrap-cycle first")

    # Step 3 — Acquire advisory lock (transaction-scoped, 2 s timeout).
    await cycles_repo.lock_advisory(cycle.id)  # raises LockBusy on timeout

    # Step 4 — Re-read cycle after holding the lock for the authoritative state.
    #           Another session may have changed it between steps 2 and 3.
    cycle = await cycles_repo.get_active()
    if cycle is None:
        raise NoActiveCycle("Active cycle disappeared after lock acquisition")

    # Step 5 — Idempotency: return early if this trigger was already applied.
    if trigger_id is not None:
        existing = await tr_repo.get_by_trigger(cycle.id, requested_to, trigger_id)
        if existing is not None:
            await session.commit()
            _emit_log(cycle.id, cycle.state, requested_to, triggered_by,
                      trigger_id, start, "already_applied")
            return TransitionResult(
                status="already_applied",
                transition_id=None,
                applied_at=existing.created_at,
                side_effect_name=None,
                cycle_id=cycle.id,
                chapter_id=cycle.chapter_id,
            )

    # Step 6 — FSM validation (pure, no I/O).
    plan: TransitionPlan = compute(
        current_state=cycle.state,
        requested_to=requested_to,
        state_entered_at=cycle.state_entered_at,
        now_utc=now_utc,
        skip_dwell=skip_dwell,
    )  # raises IllegalTransition or TimeFenceViolation

    # Step 7 — Insert transition row; ON CONFLICT DO NOTHING guards the race.
    tr = await tr_repo.insert(
        cycle_id=cycle.id,
        from_state=cycle.state,
        to_state=requested_to,
        triggered_by=triggered_by,
        trigger_id=trigger_id,
        payload_json=payload_json,
    )
    if tr is None:
        # Extremely tight race: advisory lock should have prevented this.
        # Handle gracefully as already_applied.
        fallback_at = now_utc
        if trigger_id is not None:
            maybe = await tr_repo.get_by_trigger(cycle.id, requested_to, trigger_id)
            if maybe is not None:
                fallback_at = maybe.created_at
        await session.commit()
        _emit_log(cycle.id, cycle.state, requested_to, triggered_by,
                  trigger_id, start, "already_applied_race")
        return TransitionResult(
            status="already_applied",
            transition_id=None,
            applied_at=fallback_at,
            side_effect_name=None,
            cycle_id=cycle.id,
            chapter_id=cycle.chapter_id,
        )

    # Step 8 — Persist the new cycle state.
    await cycles_repo.update_state(cycle.id, requested_to)

    # Step 9 — Apply chapter effects from TransitionPlan.state_updates.
    #           Currently only PENDING_RELEASE → ESTRENO needs this.
    #
    #           The chapter to release is the one generated during the
    #           previous cycle, held in ``next_chapter_id``. Mark IT live
    #           and advance the cycle pointer to it. Fall back to the
    #           current chapter_id for the very first bootstrap release
    #           (no next_chapter_id yet).
    if plan.state_updates.get("chapter_status") == "live":
        release_id = cycle.next_chapter_id or cycle.chapter_id
        await ch_repo.mark_live(release_id)
        await cycles_repo.advance_chapter(cycle.id)

    # Step 10 — Commit → advisory lock released, all changes atomic.
    await session.commit()

    _emit_log(cycle.id, cycle.state, requested_to, triggered_by,
              trigger_id, start, "applied")

    return TransitionResult(
        status="applied",
        transition_id=tr.id,
        applied_at=tr.created_at,
        side_effect_name=plan.side_effect,
        cycle_id=cycle.id,
        chapter_id=cycle.chapter_id,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _emit_log(
    cycle_id: int,
    from_state: str,
    to_state: str,
    triggered_by: str,
    trigger_id: str | None,
    start_monotonic: float,
    outcome: str,
) -> None:
    """FR-015: structured log event on every transition attempt."""
    duration_ms = round((time.monotonic() - start_monotonic) * 1000)
    logger.info(
        "state_transition cycle_id=%d from=%s to=%s by=%s "
        "tid=%s duration_ms=%d outcome=%s",
        cycle_id,
        from_state,
        to_state,
        triggered_by,
        trigger_id,
        duration_ms,
        outcome,
    )
