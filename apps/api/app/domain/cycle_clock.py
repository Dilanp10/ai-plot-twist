"""TZ-aware schedule helper for the daily cycle.

Module 003 / Task T-004.

ART (America/Argentina/Buenos_Aires) is UTC-3 with no DST since 1992.
We use a fixed UTC-3 offset rather than a named zoneinfo key so that:
  a) no external ``tzdata`` package is required in CI/Windows;
  b) the offset is explicit in the source rather than resolved from a DB;
  c) if Argentina ever re-introduces DST, the tests in test_cycle_clock.py
     will catch a drift immediately (Gate 3 — TZ anchoring).

Cron schedule (four ticks per day):
  12:00 ART → ESTRENO       (chapter release)
  18:00 ART → FILTERING     (submit to director filter)
  23:00 ART → GENERACION    (start generation pipeline)
  23:55 ART → WATCHDOG      (stuck-cycle health check)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Literal

# ---------------------------------------------------------------------------
# Timezone
# ---------------------------------------------------------------------------

# ART = UTC-3.  Frozen since 1992; updated here if reality diverges.
_ART_TZ: timezone = timezone(timedelta(hours=-3))


def to_art(when_utc: datetime) -> datetime:
    """Convert *when_utc* to ART (UTC-3).

    Naive datetimes are assumed to be UTC.
    """
    if when_utc.tzinfo is None:
        when_utc = when_utc.replace(tzinfo=UTC)
    return when_utc.astimezone(_ART_TZ)


# ---------------------------------------------------------------------------
# Schedule slots
# ---------------------------------------------------------------------------

TickName = Literal["ESTRENO", "FILTERING", "GENERACION", "WATCHDOG"]


@dataclass(frozen=True)
class ScheduleSlot:
    """A named tick with its ART fire time."""

    tick: TickName
    art_local_time: str  # "HH:MM"


@dataclass(frozen=True)
class ScheduleSlotInstance:
    """A concrete firing of a ``ScheduleSlot``, anchored to a UTC timestamp."""

    slot: ScheduleSlot
    fires_at_utc: datetime  # always tz-aware UTC


# The four daily ticks, in chronological order.
TICK_SLOTS: list[ScheduleSlot] = [
    ScheduleSlot(tick="ESTRENO", art_local_time="12:00"),
    ScheduleSlot(tick="FILTERING", art_local_time="18:00"),
    ScheduleSlot(tick="GENERACION", art_local_time="23:00"),
    ScheduleSlot(tick="WATCHDOG", art_local_time="23:55"),
]


def _slot_utc(slot: ScheduleSlot, art_date: datetime) -> datetime:
    """Return the UTC datetime when *slot* fires on *art_date*.

    *art_date* must be a date-only datetime (hour/minute/second = 0)
    with ``tzinfo`` set to ``_ART_TZ``.
    """
    h, m = (int(p) for p in slot.art_local_time.split(":"))
    fires_art = art_date.replace(hour=h, minute=m, second=0, microsecond=0)
    return fires_art.astimezone(UTC)


def next_n_ticks(now_utc: datetime, n: int) -> list[ScheduleSlotInstance]:
    """Return the next *n* tick instances strictly after *now_utc*.

    The list is ordered chronologically.  Scans forward day by day
    (ART calendar date) until *n* future ticks have been collected.
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)

    results: list[ScheduleSlotInstance] = []
    art_now = to_art(now_utc)
    # Anchor to midnight ART of today's date.
    art_midnight = art_now.replace(hour=0, minute=0, second=0, microsecond=0)

    while len(results) < n:
        for slot in TICK_SLOTS:
            fires_utc = _slot_utc(slot, art_midnight)
            if fires_utc > now_utc:
                results.append(ScheduleSlotInstance(slot=slot, fires_at_utc=fires_utc))
                if len(results) == n:
                    break
        art_midnight += timedelta(days=1)

    return results


# ---------------------------------------------------------------------------
# Expected-state helper (used by watchdog)
# ---------------------------------------------------------------------------

_STATE_MAP: list[tuple[int, str]] = [
    # (minute threshold exclusive upper-bound, state)
    (12 * 60,       "PENDING_RELEASE"),
    (12 * 60 + 1,   "ESTRENO"),         # 60 s auto-transition window
    (18 * 60,       "RECEPCION_IDEAS"),
    (23 * 60,       "FILTERING"),        # or VOTACION after filter completes
    (24 * 60,       "GENERACION"),       # or PENDING_RELEASE after generation
]


def expected_state_at(when_utc: datetime) -> str:
    """Return the FSM state the cycle is expected to be in at *when_utc*.

    Based solely on which cron ticks have fired (ART time-of-day).
    Does NOT account for side-effect-triggered transitions
    (FILTERING→VOTACION, GENERACION→PENDING_RELEASE) — those happen
    asynchronously and cannot be predicted from clock time alone.

    Used by the watchdog to classify a cycle as healthy or stuck.
    """
    art = to_art(when_utc)
    mins = art.hour * 60 + art.minute

    for threshold, state in _STATE_MAP:
        if mins < threshold:
            return state
    return "GENERACION"  # pragma: no cover — mins < 24*60 always
