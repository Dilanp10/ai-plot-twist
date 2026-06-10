"""Unit tests: server-computed window timestamps.

Module 004 / Task T-002.

Pure-function tests — no DB, no clock dependency beyond the explicit
``now_utc`` parameter.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone

import pytest

from app.domain.cycle_clock import TICK_SLOTS
from app.domain.windows import CycleTimes, Windows, compute_windows

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ART = timezone(timedelta(hours=-3))

# An arbitrary cycle day used across most tests.
_DAY: date = date(2026, 6, 8)
_NOW: datetime = datetime(2026, 6, 8, 14, 0, tzinfo=UTC)  # 11:00 ART


def _ct() -> CycleTimes:
    return CycleTimes.default()


def _utc(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Default config wiring (Decision 1A — reuse TICK_SLOTS)
# ---------------------------------------------------------------------------


def test_default_cycle_times_reads_from_tick_slots() -> None:
    ct = CycleTimes.default()
    by_name = {s.tick: s.art_local_time for s in TICK_SLOTS}
    assert ct.estreno_art.strftime("%H:%M") == by_name["ESTRENO"]
    assert ct.filtering_art.strftime("%H:%M") == by_name["FILTERING"]
    assert ct.generacion_art.strftime("%H:%M") == by_name["GENERACION"]


# ---------------------------------------------------------------------------
# State-by-state expectations
# ---------------------------------------------------------------------------
# For cycle_date = 2026-06-08, ART times 12:00 / 18:00 / 23:00 map to:
#   estreno  = 2026-06-08T15:00:00Z
#   filtering = 2026-06-08T21:00:00Z
#   generacion = 2026-06-09T02:00:00Z
# next_release = next-day estreno = 2026-06-09T15:00:00Z
# ---------------------------------------------------------------------------


def test_pending_release_points_to_next_day() -> None:
    """PENDING_RELEASE: all milestones live on cycle_date + 1."""
    se = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)
    w = compute_windows("PENDING_RELEASE", se, _DAY, _NOW, _ct())
    # cycle_date + 1 = 2026-06-09 ART → filtering 21:00 UTC, etc.
    assert w.submit_until == _utc(2026, 6, 9, 21)
    assert w.vote_from == _utc(2026, 6, 9, 21)
    assert w.vote_until == _utc(2026, 6, 10, 2)
    assert w.next_release == _utc(2026, 6, 10, 15)


def test_estreno_today_milestones() -> None:
    se = datetime(2026, 6, 8, 15, 0, tzinfo=UTC)
    w = compute_windows("ESTRENO", se, _DAY, _NOW, _ct())
    assert w.submit_until == _utc(2026, 6, 8, 21)
    assert w.vote_from == _utc(2026, 6, 8, 21)
    assert w.vote_until == _utc(2026, 6, 9, 2)
    assert w.next_release == _utc(2026, 6, 9, 15)


def test_recepcion_ideas_today_milestones() -> None:
    se = datetime(2026, 6, 8, 15, 1, tzinfo=UTC)
    w = compute_windows("RECEPCION_IDEAS", se, _DAY, _NOW, _ct())
    assert w.submit_until == _utc(2026, 6, 8, 21)
    assert w.vote_from == _utc(2026, 6, 8, 21)
    assert w.vote_until == _utc(2026, 6, 9, 2)
    assert w.next_release == _utc(2026, 6, 9, 15)


def test_filtering_today_milestones() -> None:
    se = datetime(2026, 6, 8, 21, 0, tzinfo=UTC)
    w = compute_windows("FILTERING", se, _DAY, _NOW, _ct())
    # submit_until is now in the past relative to the FSM state but the
    # contract still surfaces the original deadline.
    assert w.submit_until == _utc(2026, 6, 8, 21)
    assert w.vote_from == _utc(2026, 6, 8, 21)
    assert w.vote_until == _utc(2026, 6, 9, 2)
    assert w.next_release == _utc(2026, 6, 9, 15)


def test_votacion_today_milestones() -> None:
    se = datetime(2026, 6, 8, 21, 5, tzinfo=UTC)
    w = compute_windows("VOTACION", se, _DAY, _NOW, _ct())
    assert w.submit_until == _utc(2026, 6, 8, 21)
    assert w.vote_from == _utc(2026, 6, 8, 21)
    assert w.vote_until == _utc(2026, 6, 9, 2)
    assert w.next_release == _utc(2026, 6, 9, 15)


def test_generacion_today_milestones() -> None:
    se = datetime(2026, 6, 9, 2, 0, tzinfo=UTC)
    w = compute_windows("GENERACION", se, _DAY, _NOW, _ct())
    assert w.submit_until == _utc(2026, 6, 8, 21)
    assert w.vote_from == _utc(2026, 6, 8, 21)
    assert w.vote_until == _utc(2026, 6, 9, 2)
    assert w.next_release == _utc(2026, 6, 9, 15)


# ---------------------------------------------------------------------------
# FAILED — Decision 2B: freeze all windows at state_entered_at
# ---------------------------------------------------------------------------


def test_failed_freezes_all_windows_at_state_entered_at() -> None:
    se = datetime(2026, 6, 8, 23, 30, tzinfo=UTC)
    w = compute_windows("FAILED", se, _DAY, _NOW, _ct())
    assert w.submit_until == se
    assert w.vote_from == se
    assert w.vote_until == se
    assert w.next_release == se


def test_failed_with_naive_state_entered_at_assumed_utc() -> None:
    se_naive = datetime(2026, 6, 8, 23, 30)  # no tzinfo
    w = compute_windows("FAILED", se_naive, _DAY, _NOW, _ct())
    expected = se_naive.replace(tzinfo=UTC)
    assert w.submit_until == expected


def test_failed_with_art_tz_normalized_to_utc() -> None:
    se_art = datetime(2026, 6, 8, 20, 30, tzinfo=_ART)  # 23:30 UTC
    w = compute_windows("FAILED", se_art, _DAY, _NOW, _ct())
    assert w.submit_until == datetime(2026, 6, 8, 23, 30, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Property: next_release advances by exactly +1 day vs the PENDING_RELEASE
# perspective of the same calendar position. Mirrors tasks.md note:
# "next_release advances by 1 day after the current state passes ESTRENO".
# ---------------------------------------------------------------------------


def test_next_release_advances_one_day_after_estreno_passes() -> None:
    """PENDING_RELEASE on day D and ESTRENO on day D+1 should agree on the
    same wall-clock ``next_release``.

    PENDING_RELEASE(D)  → next_release = (D+1) + 1 day estreno = D+2 estreno
    ESTRENO       (D+1) → next_release = (D+1) + 1 day estreno = D+2 estreno
    """
    se = datetime(2026, 6, 8, 12, 0, tzinfo=UTC)
    w_pending = compute_windows("PENDING_RELEASE", se, _DAY, _NOW, _ct())
    w_estreno = compute_windows("ESTRENO", se, _DAY + timedelta(days=1), _NOW, _ct())
    assert w_pending.next_release == w_estreno.next_release


# ---------------------------------------------------------------------------
# TZ correctness: ART → UTC conversion (UTC-3, no DST)
# ---------------------------------------------------------------------------


def test_art_to_utc_conversion_is_minus_three() -> None:
    """12:00 ART on a given calendar date must map to 15:00 UTC same date."""
    w = compute_windows("ESTRENO", _NOW, _DAY, _NOW, _ct())
    # next_release for ESTRENO on 2026-06-08 = 2026-06-09 12:00 ART
    assert w.next_release == _utc(2026, 6, 9, 15)


def test_now_utc_does_not_affect_windows_decision_3a() -> None:
    """Decision 3A: windows are deterministic regardless of ``now_utc``."""
    se = datetime(2026, 6, 8, 15, 0, tzinfo=UTC)
    early = datetime(2026, 6, 8, 0, 0, tzinfo=UTC)
    late = datetime(2026, 6, 9, 23, 59, tzinfo=UTC)
    w_early = compute_windows("RECEPCION_IDEAS", se, _DAY, early, _ct())
    w_late = compute_windows("RECEPCION_IDEAS", se, _DAY, late, _ct())
    assert w_early == w_late


# ---------------------------------------------------------------------------
# Custom CycleTimes — independence from cron defaults
# ---------------------------------------------------------------------------


def test_custom_cycle_times_drive_milestones() -> None:
    """Tests can plug arbitrary times without monkeypatching TICK_SLOTS."""
    ct = CycleTimes(
        estreno_art=time(10, 0),
        filtering_art=time(16, 0),
        generacion_art=time(22, 0),
    )
    w = compute_windows("RECEPCION_IDEAS", _NOW, _DAY, _NOW, ct)
    assert w.submit_until == _utc(2026, 6, 8, 19)  # 16:00 ART = 19:00 UTC
    assert w.vote_until == _utc(2026, 6, 9, 1)  # 22:00 ART = 01:00 UTC +1
    assert w.next_release == _utc(2026, 6, 9, 13)  # 10:00 ART next day = 13:00 UTC


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_unknown_state_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown cycle_state"):
        compute_windows("BOGUS_STATE", _NOW, _DAY, _NOW, _ct())


# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------


def test_returns_windows_dataclass_with_utc_datetimes() -> None:
    w = compute_windows("RECEPCION_IDEAS", _NOW, _DAY, _NOW, _ct())
    assert isinstance(w, Windows)
    for field_value in (w.submit_until, w.vote_from, w.vote_until, w.next_release):
        assert field_value.tzinfo is not None
        assert field_value.utcoffset() == timedelta(0)
