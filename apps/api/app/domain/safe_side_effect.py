"""Safety wrapper for background side effects.

Module 003 / Task T-013.

Implements R-008: when a side effect (director_filter, generation_pipeline)
raises any exception, the wrapper:

  1. Logs the full traceback at CRITICAL level.
  2. Forces the cycle → FAILED state and inserts a transition record with
     ``error_hash``, ``error_type``, and ``side_effect`` in payload_json.
  3. Sets ``kill_switch = {on: True, reason: ...}`` to block further ticks
     while the PO investigates.
  4. Clears the in-process flag cache so the next executor call reads fresh.
  5. POSTs a Discord webhook alert (if ``discord_webhook_url`` is not None)
     with a copy-pasteable ``pnpm kill-switch --off`` recovery command.
  6. Returns normally — this is a fire-and-forget BackgroundTask; the HTTP
     response was already sent.

Privacy: only ``error_hash`` and ``error_type`` go to the DB.
The full error message is written to logs only (Fly log drain).

Usage (from the HTTP route handler)::

    if result.side_effect_name:
        background_tasks.add_task(
            safe_side_effect.run_safe,
            name=result.side_effect_name,
            chapter_id=result.chapter_id,
            cycle_id=result.cycle_id,
            session_factory=app_state.session_factory,
            discord_webhook_url=settings.discord_webhook_url or None,
        )
"""

from __future__ import annotations

import hashlib
import logging
import traceback
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain import side_effects
from app.infra.cycles_repo import CyclesRepo
from app.infra.system_flags_repo import SystemFlagsRepo, clear_cache
from app.infra.transitions_repo import TransitionsRepo

__all__ = ["run_safe"]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_safe(
    name: str,
    chapter_id: int,
    cycle_id: int,
    session_factory: async_sessionmaker[AsyncSession],
    discord_webhook_url: str | None = None,
) -> None:
    """Execute side effect *name* with a full crash-recovery safety net.

    Parameters
    ----------
    name:
        Registry key (e.g. ``"director_filter"``).
    chapter_id:
        Passed as the sole argument to the side-effect function.
    cycle_id:
        Used for the FAILED transition record and Discord alert.
    session_factory:
        ``async_sessionmaker`` for creating a **fresh** session inside the
        failure handler (the request session is already committed).
    discord_webhook_url:
        Discord Incoming Webhook URL.  Pass *None* to skip the alert.

    Returns
    -------
    None
        Always — the function handles all exceptions internally.
    """
    fn = side_effects.get(name)
    try:
        await fn(chapter_id)
    except Exception as exc:
        error_str = str(exc)
        error_hash = hashlib.sha256(error_str.encode()).hexdigest()[:8]
        error_type = type(exc).__name__
        tb = traceback.format_exc()

        logger.critical(
            "side_effect_crash name=%s chapter_id=%d cycle_id=%d "
            "error_hash=%s error_type=%s\n%s",
            name,
            chapter_id,
            cycle_id,
            error_hash,
            error_type,
            tb,
        )

        await _handle_failure(
            cycle_id=cycle_id,
            session_factory=session_factory,
            side_effect_name=name,
            error_hash=error_hash,
            error_type=error_type,
        )

        if discord_webhook_url is not None:
            await _post_discord(
                webhook_url=discord_webhook_url,
                name=name,
                chapter_id=chapter_id,
                cycle_id=cycle_id,
                error_hash=error_hash,
                error_type=error_type,
                error_msg=error_str[:500],
            )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _handle_failure(
    cycle_id: int,
    session_factory: async_sessionmaker[AsyncSession],
    side_effect_name: str,
    error_hash: str,
    error_type: str,
) -> None:
    """Force the cycle to FAILED, activate kill-switch, commit.

    Any exception here is caught and logged as CRITICAL — we must not
    propagate errors out of a BackgroundTask crash handler.
    """
    try:
        async with session_factory() as session:
            cycles_repo = CyclesRepo(session)
            tr_repo = TransitionsRepo(session)
            flags_repo = SystemFlagsRepo(session)

            # Determine the current (pre-FAILED) state for the from_state field.
            cycle = await cycles_repo.get_active()
            from_state = cycle.state if cycle is not None else "UNKNOWN"

            # Insert a FAILED transition row with sanitised error metadata
            # (no raw error message — only hash + type go to the DB).
            await tr_repo.insert(
                cycle_id=cycle_id,
                from_state=from_state,
                to_state="FAILED",
                triggered_by="safe_side_effect",
                trigger_id=None,
                payload_json={
                    "error_hash": error_hash,
                    "error_type": error_type,
                    "side_effect": side_effect_name,
                },
            )

            # Force the cycle state to FAILED.
            await cycles_repo.update_state(cycle_id, "FAILED")

            # Activate the kill-switch to stop further ticks.
            await flags_repo.set(
                "kill_switch",
                {
                    "on": True,
                    "reason": f"side_effect_failed:{side_effect_name}",
                    "error_hash": error_hash,
                    "cycle_id": cycle_id,
                },
                updated_by="safe_side_effect",
            )
            # Flush in-process cache so the next executor call reads fresh.
            clear_cache()

            await session.commit()

            logger.info(
                "safe_side_effect: forced FAILED + kill-switch "
                "cycle_id=%d side_effect=%s",
                cycle_id,
                side_effect_name,
            )

    except Exception as inner:
        # Do NOT reraise — a double-failure would leave no trace in the logs.
        logger.critical(
            "safe_side_effect: CRITICAL — could not force FAILED state "
            "for cycle_id=%d: %r",
            cycle_id,
            inner,
        )


async def _post_discord(
    webhook_url: str,
    name: str,
    chapter_id: int,
    cycle_id: int,
    error_hash: str,
    error_type: str,
    error_msg: str,
) -> None:
    """POST an alert to a Discord Incoming Webhook.

    Failure is silently logged — a broken webhook must not mask the
    original crash or the FAILED state already written to the DB.
    """
    body: dict[str, Any] = {
        "content": (
            f"🚨 **Side-effect crash** — `cycle_id={cycle_id}` forced to "
            f"**FAILED**, kill-switch **ON**\n\n"
            f"**Side effect**: `{name}`\n"
            f"**Chapter**: `{chapter_id}`\n"
            f"**Error**: `{error_type}` (hash `{error_hash}`)\n"
            f"**Message**: {error_msg}\n\n"
            f"**Recovery**:\n"
            f"```\n"
            f"pnpm kill-switch --off\n"
            f"# inspect logs, then replay if safe:\n"
            f"pnpm replay-tick --to FILTERING   # or GENERACION\n"
            f"```"
        )
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=body)
            resp.raise_for_status()
    except Exception as exc:
        logger.warning(
            "safe_side_effect: Discord webhook POST failed: %r", exc
        )
