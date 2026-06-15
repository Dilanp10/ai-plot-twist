"""Pure finite-state machine for the daily cycle.

Module 003 / Task T-005.

No DB I/O — only state-transition logic and time-fence validation.
The executor (``cycle_executor.py``) is responsible for all I/O and
side-effect dispatch.

FR-004 (single source of truth for legal transitions):
  PENDING_RELEASE → ESTRENO           (cron @ 12:00)
  ESTRENO         → RECEPCION_IDEAS   (auto-tick after 60 s)
  RECEPCION_IDEAS → FILTERING         (cron @ 18:00 → spawns director_filter)
  FILTERING       → VOTACION          (director_filter task complete)
  FILTERING       → FAILED            (director_filter failure / retries exhausted)
  VOTACION        → GENERACION        (cron @ 23:00 → spawns generation_pipeline)
  GENERACION      → PENDING_RELEASE   (generation_pipeline task complete)
  GENERACION      → FAILED            (generation_pipeline timeout / failure)

Admin "any → any" override is handled at the executor level and does NOT
go through ``compute()``.

FR-005 (min-dwell times):
  PENDING_RELEASE   0 s
  ESTRENO           60 s
  RECEPCION_IDEAS   5 h 30 min  (19 800 s)
  FILTERING         1 s
  VOTACION          4 h 45 min  (17 100 s)
  GENERACION        30 min      (1 800 s)
  FAILED            0 s         (terminal; no out-edges)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Minimum seconds a cycle must spend in each state before leaving it.
MIN_DWELL_SECONDS: dict[str, int] = {
    "PENDING_RELEASE": 0,
    "ESTRENO": 60,
    "RECEPCION_IDEAS": 19_800,  # 5 h 30 min
    "FILTERING": 1,
    "VOTACION": 17_100,  # 4 h 45 min
    "GENERACION": 1_800,  # 30 min
    "FAILED": 0,  # terminal state — no out-edges but defined for completeness
}

ALL_STATES: frozenset[str] = frozenset(MIN_DWELL_SECONDS)


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransitionPlan:
    """Result of a successful ``compute()`` call.

    Attributes:
        to: Target FSM state.
        side_effect: Name of the DI-registered side effect to spawn as a
            ``BackgroundTask``, or *None* if this transition has no side effect.
        state_updates: Executor hints — arbitrary key/value pairs describing
            additional DB mutations the executor should perform alongside the
            state update.  Content is transition-specific (see ``_EDGE``).
    """

    to: str
    side_effect: str | None
    state_updates: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class IllegalTransition(Exception):
    """(from_state, to_state) is not a legal FSM edge."""

    def __init__(self, from_state: str, to_state: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"Illegal FSM transition: {from_state!r} → {to_state!r}"
        )


class TimeFenceViolation(Exception):
    """Transition is legal but min_dwell has not yet elapsed."""

    def __init__(
        self,
        from_state: str,
        to_state: str,
        elapsed_s: float,
        min_dwell_s: int,
        earliest_at: datetime,
    ) -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.elapsed_s = elapsed_s
        self.min_dwell_s = min_dwell_s
        self.earliest_at = earliest_at
        super().__init__(
            f"TimeFenceViolation: {from_state!r} → {to_state!r} requires "
            f"{min_dwell_s} s dwell; only {elapsed_s:.1f} s elapsed. "
            f"Earliest valid at: {earliest_at.isoformat()}"
        )


# ---------------------------------------------------------------------------
# Transition edge table
# ---------------------------------------------------------------------------
# Key   : (from_state, to_state)
# Value : (side_effect_name | None, state_updates_dict)
#
# "any → any | admin" is handled at the executor level; it does NOT appear
# here.  All admin-bypass paths skip ``compute()`` entirely.

_EDGE: dict[tuple[str, str], tuple[str | None, dict[str, Any]]] = {
    # ── main happy-path loop ──────────────────────────────────────────────
    ("PENDING_RELEASE", "ESTRENO"): (
        "push_fanout",
        {"chapter_status": "live", "chapter_released_at": "now"},
    ),
    ("ESTRENO", "RECEPCION_IDEAS"): (None, {}),
    ("RECEPCION_IDEAS", "FILTERING"): ("director_filter", {}),
    ("FILTERING", "VOTACION"): (None, {}),
    ("VOTACION", "GENERACION"): ("generation_pipeline", {}),
    ("GENERACION", "PENDING_RELEASE"): (None, {}),
    # ── failure paths ─────────────────────────────────────────────────────
    ("FILTERING", "FAILED"): (None, {}),
    ("GENERACION", "FAILED"): (None, {}),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute(
    current_state: str,
    requested_to: str,
    state_entered_at: datetime,
    now_utc: datetime,
    *,
    skip_dwell: bool = False,
) -> TransitionPlan:
    """Validate and plan a state transition.

    Pure function — no I/O, no side-effects.

    Args:
        current_state: Current FSM state of the cycle.
        requested_to: Target state being requested.
        state_entered_at: When the cycle entered *current_state*.
            Naive datetimes are assumed UTC.
        now_utc: Current wall-clock time.
            Naive datetimes are assumed UTC.
        skip_dwell: When *True*, bypass the min_dwell time-fence check.
            Intended for admin overrides and tests that control the clock
            externally.

    Returns:
        TransitionPlan describing what the executor should do.

    Raises:
        IllegalTransition: *(current_state, requested_to)* is not in the
            legal edge table.
        TimeFenceViolation: Edge is legal but the cycle has not been in
            *current_state* for long enough.  Only raised when
            ``skip_dwell=False``.
    """
    # Normalise to tz-aware UTC for arithmetic.
    if state_entered_at.tzinfo is None:
        state_entered_at = state_entered_at.replace(tzinfo=UTC)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)

    # 1. Legal-transition check.
    edge_key = (current_state, requested_to)
    if edge_key not in _EDGE:
        raise IllegalTransition(current_state, requested_to)

    # 2. Min-dwell time-fence.
    if not skip_dwell:
        min_dwell = MIN_DWELL_SECONDS.get(current_state, 0)
        elapsed = (now_utc - state_entered_at).total_seconds()
        if elapsed < min_dwell:
            earliest_at = state_entered_at + timedelta(seconds=min_dwell)
            raise TimeFenceViolation(
                current_state,
                requested_to,
                elapsed,
                min_dwell,
                earliest_at,
            )

    side_effect, state_updates = _EDGE[edge_key]
    return TransitionPlan(
        to=requested_to,
        side_effect=side_effect,
        state_updates=state_updates,
    )
