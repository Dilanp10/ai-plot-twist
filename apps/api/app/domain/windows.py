"""Server-computed window timestamps for ``GET /chapters/today``.

Module 004 / Task T-002.

The PWA needs four absolute UTC instants to drive its UI:

  * ``submit_until``  — when the "Tirá una idea" CTA disappears.
  * ``vote_from``     — when the vote feed becomes interactive.
  * ``vote_until``    — when voting closes.
  * ``next_release``  — when the next chapter goes ``live``.

All four are derived **server-side** from the cycle's ``cycle_date`` and the
canonical ART-local schedule (see research R-004). The client never has to know
the cron schedule, the min-dwell rules, or compute timezones — which keeps the
PWA decoupled from backend internals.

Source of truth for the times-of-day is :data:`app.domain.cycle_clock.TICK_SLOTS`
(Decision 1A — DRY: changing the cron schedule changes the windows automatically).

State semantics:

* ``PENDING_RELEASE``: the cycle has not yet hit ESTRENO. All four windows
  point to the **next** day's milestones (cycle_date + 1).
* ``ESTRENO`` / ``RECEPCION_IDEAS`` / ``FILTERING`` / ``VOTACION`` /
  ``GENERACION``: today's milestones. As the cycle advances, individual
  windows pass into the past — the contract still surfaces them so the PWA can
  render "closed" badges with the original deadline.
* ``FAILED``: cycle is stuck. Per Decision 2B we freeze all four windows at
  ``state_entered_at`` so the PWA shows a sensible "frozen" timestamp instead
  of a phantom future deadline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from app.domain.cycle_clock import _ART_TZ, TICK_SLOTS

# ---------------------------------------------------------------------------
# Schedule config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CycleTimes:
    """ART local times-of-day for the three windows-relevant ticks.

    ``WATCHDOG`` (23:55) is not exposed as a window — it is internal health.
    """

    estreno_art: time
    filtering_art: time
    generacion_art: time

    @classmethod
    def default(cls) -> CycleTimes:
        """Build from :data:`cycle_clock.TICK_SLOTS` — single source of truth.

        Decision 1A: if the cron schedule changes in cycle_clock, windows
        follow automatically. Tests can construct a custom :class:`CycleTimes`
        to exercise edge cases without monkeypatching ``TICK_SLOTS``.
        """
        by_name = {s.tick: s.art_local_time for s in TICK_SLOTS}
        return cls(
            estreno_art=_parse_hhmm(by_name["ESTRENO"]),
            filtering_art=_parse_hhmm(by_name["FILTERING"]),
            generacion_art=_parse_hhmm(by_name["GENERACION"]),
        )


def _parse_hhmm(s: str) -> time:
    h, m = (int(p) for p in s.split(":"))
    return time(hour=h, minute=m)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Windows:
    """Four absolute UTC instants surfaced in :class:`TodayResponse.windows`."""

    submit_until: datetime
    vote_from: datetime
    vote_until: datetime
    next_release: datetime


# ---------------------------------------------------------------------------
# Compute
# ---------------------------------------------------------------------------

_PRE_RELEASE_STATES: frozenset[str] = frozenset({"PENDING_RELEASE"})
_ACTIVE_STATES: frozenset[str] = frozenset(
    {"ESTRENO", "RECEPCION_IDEAS", "FILTERING", "VOTACION", "GENERACION"}
)
_FAILED_STATE = "FAILED"


def compute_windows(
    cycle_state: str,
    state_entered_at: datetime,
    cycle_date: date,
    now_utc: datetime,
    cycle_times: CycleTimes,
) -> Windows:
    """Return the four UTC window instants for the given cycle.

    Parameters
    ----------
    cycle_state:
        One of the seven FSM state strings.
    state_entered_at:
        When the cycle entered ``cycle_state``. Only consulted for ``FAILED``.
    cycle_date:
        The cycle's calendar date (ART). Anchor for all milestones.
    now_utc:
        Current UTC time. **Unused** under Decision 3A: ``next_release`` is
        always derived from ``cycle_date + 1`` rather than from ``now_utc``,
        so windows are deterministic for a given cycle regardless of when the
        function is called. The parameter is retained for future flexibility
        and to match the signature documented in tasks.md T-002.
    cycle_times:
        ART times-of-day for ESTRENO / FILTERING / GENERACION.

    Returns
    -------
    Windows
        All four fields as tz-aware UTC datetimes.

    Raises
    ------
    ValueError
        If ``cycle_state`` is not one of the seven documented FSM states.
    """
    if cycle_state in _PRE_RELEASE_STATES:
        return _windows_for_day(cycle_date + timedelta(days=1), cycle_times)

    if cycle_state in _ACTIVE_STATES:
        return _windows_for_day(cycle_date, cycle_times)

    if cycle_state == _FAILED_STATE:
        frozen = _to_utc(state_entered_at)
        return Windows(
            submit_until=frozen,
            vote_from=frozen,
            vote_until=frozen,
            next_release=frozen,
        )

    raise ValueError(f"Unknown cycle_state: {cycle_state!r}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _windows_for_day(art_day: date, ct: CycleTimes) -> Windows:
    """Build a :class:`Windows` whose milestones live on ``art_day`` (ART).

    ``next_release`` is the **following** day's ESTRENO regardless: the
    "next" release is always one cycle ahead of the milestones on this day.
    """
    submit_until = _art_datetime_to_utc(art_day, ct.filtering_art)
    vote_from = submit_until
    vote_until = _art_datetime_to_utc(art_day, ct.generacion_art)
    next_release = _art_datetime_to_utc(art_day + timedelta(days=1), ct.estreno_art)
    return Windows(
        submit_until=submit_until,
        vote_from=vote_from,
        vote_until=vote_until,
        next_release=next_release,
    )


def _art_datetime_to_utc(day: date, hhmm: time) -> datetime:
    """Combine an ART calendar date + ART time-of-day → tz-aware UTC datetime."""
    art_local = datetime.combine(day, hhmm, tzinfo=_ART_TZ)
    return art_local.astimezone(UTC)


def _to_utc(when: datetime) -> datetime:
    """Normalize *when* to tz-aware UTC. Naive inputs are assumed UTC."""
    if when.tzinfo is None:
        return when.replace(tzinfo=UTC)
    return when.astimezone(UTC)
