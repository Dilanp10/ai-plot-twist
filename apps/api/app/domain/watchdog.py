"""Watchdog: schedule-aware stuck-cycle detection.

Module 003 / Task T-014.

Implements research R-004.  The watchdog runs at 23:55 ART (cron tick) and
inspects the active cycle.  Depending on the cycle's current state and time
elapsed since the last state change, it emits a verdict:

  ready_for_release  — PENDING_RELEASE (generation finished, healthy)
  ok_in_progress     — GENERACION, elapsed < 60 min (still running, healthy)
  already_failed     — FAILED (someone already handled it, no action)
  no_active_cycle    — no active season/cycle exists (no action)
  stuck_generation   — GENERACION, elapsed ≥ 60 min → forces FAILED
  stuck_voting       — VOTACION at 23:55               → forces FAILED
  stuck_filtering    — FILTERING at 23:55              → forces FAILED
  stuck_reception    — RECEPCION_IDEAS at 23:55        → forces FAILED
  impossible_state   — ESTRENO at 23:55 (should never happen)
                        → forces FAILED + logs CRITICAL

All "stuck" and "impossible" verdicts:
  1. Acquire advisory lock (same key as executor)
  2. Insert a state_transitions row with verdict + elapsed_s in payload_json
  3. Update cycle state → FAILED
  4. Commit
  5. POST Discord webhook (if configured)

The session is committed on stuck verdicts; no mutation on healthy verdicts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.cycles_repo import CyclesRepo
from app.infra.transitions_repo import TransitionsRepo

__all__ = [
    "GENERATION_GRACE_S",
    "STUCK_VERDICTS",
    "WatchdogResult",
    "check",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GENERATION_GRACE_S: int = 3_600  # 60 min — generation pipeline grace period

WatchdogVerdict = Literal[
    "ready_for_release",
    "ok_in_progress",
    "already_failed",
    "no_active_cycle",
    "stuck_generation",
    "stuck_voting",
    "stuck_filtering",
    "stuck_reception",
    "impossible_state",
]

STUCK_VERDICTS: frozenset[str] = frozenset({
    "stuck_generation",
    "stuck_voting",
    "stuck_filtering",
    "stuck_reception",
    "impossible_state",
})

# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WatchdogResult:
    """Outcome of a ``check()`` call."""

    verdict: WatchdogVerdict
    cycle_id: int | None         # None when no active cycle
    cycle_state: str | None      # pre-transition state (original stuck state)
    elapsed_seconds: float | None
    forced_failed: bool          # True if a FAILED transition was committed
    discord_posted: bool         # True if _post_discord was invoked


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def check(
    session: AsyncSession,
    now_utc: datetime,
    discord_webhook_url: str | None = None,
) -> WatchdogResult:
    """Run the watchdog check.

    Commits *session* when forcing a FAILED transition.  On healthy verdicts
    the session is untouched (caller may rollback or let the context manager
    handle it).

    Parameters
    ----------
    session:
        Active ``AsyncSession`` from the FastAPI DI container.
    now_utc:
        Current UTC time.  Injected for testability; callers pass
        ``datetime.now(UTC)``.
    discord_webhook_url:
        Discord Incoming Webhook URL.  *None* skips the alert.

    Returns
    -------
    WatchdogResult
    """
    cycles_repo = CyclesRepo(session)
    tr_repo = TransitionsRepo(session)

    # 1 — Get active cycle.
    cycle = await cycles_repo.get_active()
    if cycle is None:
        logger.info("watchdog_check: no active cycle")
        return WatchdogResult(
            verdict="no_active_cycle",
            cycle_id=None,
            cycle_state=None,
            elapsed_seconds=None,
            forced_failed=False,
            discord_posted=False,
        )

    # 2 — Compute elapsed time in this state (handle naive datetimes from DB).
    entered_at = cycle.state_entered_at
    if entered_at.tzinfo is None:
        entered_at = entered_at.replace(tzinfo=UTC)
    elapsed_s = (now_utc - entered_at).total_seconds()

    # 3 — Verdict.
    verdict: WatchdogVerdict = _compute_verdict(cycle.state, elapsed_s)

    logger.info(
        "watchdog_check cycle_id=%d state=%s elapsed_s=%.1f verdict=%s",
        cycle.id,
        cycle.state,
        elapsed_s,
        verdict,
    )

    # 4 — Healthy verdicts: return immediately, no mutation.
    if verdict not in STUCK_VERDICTS:
        return WatchdogResult(
            verdict=verdict,
            cycle_id=cycle.id,
            cycle_state=cycle.state,
            elapsed_seconds=elapsed_s,
            forced_failed=False,
            discord_posted=False,
        )

    # 5 — Stuck / impossible: force FAILED.
    if verdict == "impossible_state":
        logger.critical(
            "watchdog_check IMPOSSIBLE STATE: cycle_id=%d state=%s "
            "elapsed_s=%.1f — forcing FAILED",
            cycle.id,
            cycle.state,
            elapsed_s,
        )

    await cycles_repo.lock_advisory(cycle.id)

    # Re-read after holding the lock (another session may have transitioned).
    cycle = await cycles_repo.get_active()
    if cycle is None or cycle.state == "FAILED":
        # Already handled by another process.
        await session.rollback()
        logger.info(
            "watchdog_check: cycle already FAILED or disappeared after lock "
            "cycle_id=%s",
            cycle.id if cycle is not None else "N/A",
        )
        return WatchdogResult(
            verdict="already_failed",
            cycle_id=cycle.id if cycle is not None else None,
            cycle_state=cycle.state if cycle is not None else None,
            elapsed_seconds=elapsed_s,
            forced_failed=False,
            discord_posted=False,
        )

    await tr_repo.insert(
        cycle_id=cycle.id,
        from_state=cycle.state,
        to_state="FAILED",
        triggered_by="watchdog",
        trigger_id=None,
        payload_json={"verdict": verdict, "elapsed_s": round(elapsed_s, 1)},
    )
    await cycles_repo.update_state(cycle.id, "FAILED")
    await session.commit()

    logger.info(
        "watchdog_check: forced FAILED cycle_id=%d original_state=%s verdict=%s",
        cycle.id,
        cycle.state,
        verdict,
    )

    # 6 — Discord alert.
    discord_posted = False
    if discord_webhook_url is not None:
        discord_posted = await _post_discord(
            webhook_url=discord_webhook_url,
            verdict=verdict,
            cycle_id=cycle.id,
            cycle_state=cycle.state,
            elapsed_s=elapsed_s,
        )

    return WatchdogResult(
        verdict=verdict,
        cycle_id=cycle.id,
        cycle_state=cycle.state,
        elapsed_seconds=elapsed_s,
        forced_failed=True,
        discord_posted=discord_posted,
    )


# ---------------------------------------------------------------------------
# Verdict computation (pure)
# ---------------------------------------------------------------------------


def _compute_verdict(state: str, elapsed_s: float) -> WatchdogVerdict:
    """Map cycle state + elapsed seconds to a watchdog verdict (R-004 table)."""
    if state == "PENDING_RELEASE":
        return "ready_for_release"
    if state == "GENERACION":
        return "ok_in_progress" if elapsed_s < GENERATION_GRACE_S else "stuck_generation"
    if state == "FAILED":
        return "already_failed"
    if state == "VOTACION":
        return "stuck_voting"
    if state == "FILTERING":
        return "stuck_filtering"
    if state == "RECEPCION_IDEAS":
        return "stuck_reception"
    # ESTRENO at 23:55 is impossible: it should have advanced hours ago.
    return "impossible_state"


# ---------------------------------------------------------------------------
# Discord helper
# ---------------------------------------------------------------------------


async def _post_discord(
    webhook_url: str,
    verdict: str,
    cycle_id: int,
    cycle_state: str,
    elapsed_s: float,
) -> bool:
    """POST a watchdog alert to Discord.

    Returns True if the POST succeeded, False otherwise (errors are logged,
    never reraised — a broken webhook must not block the watchdog response).
    """
    elapsed_min = round(elapsed_s / 60, 1)
    body: dict[str, Any] = {
        "content": (
            f"⏰ **Watchdog alert** — `cycle_id={cycle_id}` forced to **FAILED**\n\n"
            f"**Verdict**: `{verdict}`\n"
            f"**Stuck state**: `{cycle_state}`\n"
            f"**Elapsed**: {elapsed_min} min\n\n"
            f"**Recovery**:\n"
            f"```\n"
            f"pnpm kill-switch --off\n"
            f"pnpm replay-tick --to {cycle_state}  # re-enter the stuck state\n"
            f"```"
        )
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=body)
            resp.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("watchdog: Discord webhook POST failed: %r", exc)
        return False
