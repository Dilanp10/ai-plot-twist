"""Unit tests: cycle_clock TZ helper.

Module 003 / Task T-004.

All assertions are pure-function: no DB, no network.
Times are fed explicitly so the suite never depends on wall-clock.

DST-boundary defense (Gate 3):
  Even though ART has not had DST since 1992, we verify the UTC-ART
  offset is exactly -3 h for both the summer and winter solstice to
  catch any accidental future drift.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta, timezone

from app.domain.cycle_clock import (
    TICK_SLOTS,
    ScheduleSlotInstance,
    expected_state_at,
    next_n_ticks,
    to_art,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ART_OFFSET = timezone(timedelta(hours=-3))


def _utc(y: int, mo: int, d: int, h: int = 0, m: int = 0, s: int = 0) -> datetime:
    return datetime(y, mo, d, h, m, s, tzinfo=UTC)


def _art_time(y: int, mo: int, d: int, h: int, m: int) -> datetime:
    """Return a timezone-aware ART datetime (UTC-3)."""
    return datetime(y, mo, d, h, m, tzinfo=_ART_OFFSET)


# ---------------------------------------------------------------------------
# to_art
# ---------------------------------------------------------------------------


class TestToArt:
    def test_utc15_becomes_art12(self) -> None:
        """15:00 UTC = 12:00 ART (UTC-3)."""
        utc = _utc(2026, 6, 9, 15, 0)
        art = to_art(utc)
        assert art.hour == 12
        assert art.minute == 0

    def test_utc_midnight_becomes_art_2100_prev_day(self) -> None:
        """00:00 UTC = 21:00 ART on the *previous* day."""
        utc = _utc(2026, 6, 10, 0, 0)
        art = to_art(utc)
        assert art.day == 9
        assert art.hour == 21
        assert art.minute == 0

    def test_naive_datetime_treated_as_utc(self) -> None:
        """Naive datetimes are assumed UTC (not local time)."""
        naive = datetime(2026, 6, 9, 15, 0)
        art = to_art(naive)
        assert art.hour == 12  # same as explicit UTC

    def test_preserves_tz_awareness(self) -> None:
        result = to_art(_utc(2026, 1, 1, 12, 0))
        assert result.tzinfo is not None

    def test_roundtrip_utc(self) -> None:
        """to_art then back to UTC must equal the original."""
        utc = _utc(2026, 6, 9, 18, 30)
        art = to_art(utc)
        back_utc = art.astimezone(UTC)
        assert back_utc == utc

    # -- DST-boundary defense (Gate 3) ------------------------------------

    def test_offset_at_summer_solstice(self) -> None:
        """Dec 21 (southern summer) offset = -3 h."""
        utc = _utc(2026, 12, 21, 12, 0)
        art = to_art(utc)
        offset_hours = art.utcoffset().total_seconds() / 3600  # type: ignore[union-attr]
        assert offset_hours == -3

    def test_offset_at_winter_solstice(self) -> None:
        """Jun 21 (southern winter) offset = -3 h."""
        utc = _utc(2026, 6, 21, 12, 0)
        art = to_art(utc)
        offset_hours = art.utcoffset().total_seconds() / 3600  # type: ignore[union-attr]
        assert offset_hours == -3

    def test_offset_matches_across_year(self) -> None:
        """Spot-check 12 months: UTC offset is always exactly -3 h."""
        for month in range(1, 13):
            utc = _utc(2026, month, 15, 12, 0)
            art = to_art(utc)
            hrs = art.utcoffset().total_seconds() / 3600  # type: ignore[union-attr]
            assert hrs == -3, f"Month {month}: offset {hrs} != -3"


# ---------------------------------------------------------------------------
# expected_state_at
# ---------------------------------------------------------------------------


class TestExpectedStateAt:
    """Each major ART time window maps to the right FSM state."""

    def _state(self, h: int, m: int = 0) -> str:
        """Return expected_state for today at h:m ART (via UTC conversion)."""
        art = _art_time(2026, 6, 9, h, m)
        utc = art.astimezone(UTC)
        return expected_state_at(utc)

    def test_before_noon_is_pending_release(self) -> None:
        assert self._state(11, 59) == "PENDING_RELEASE"

    def test_0000_is_pending_release(self) -> None:
        assert self._state(0, 0) == "PENDING_RELEASE"

    def test_1200_is_estreno(self) -> None:
        assert self._state(12, 0) == "ESTRENO"

    def test_1201_is_recepcion_ideas(self) -> None:
        assert self._state(12, 1) == "RECEPCION_IDEAS"

    def test_1300_is_recepcion_ideas(self) -> None:
        assert self._state(13, 0) == "RECEPCION_IDEAS"

    def test_1759_is_recepcion_ideas(self) -> None:
        assert self._state(17, 59) == "RECEPCION_IDEAS"

    def test_1800_is_filtering(self) -> None:
        assert self._state(18, 0) == "FILTERING"

    def test_2000_is_filtering(self) -> None:
        assert self._state(20, 0) == "FILTERING"

    def test_2259_is_filtering(self) -> None:
        assert self._state(22, 59) == "FILTERING"

    def test_2300_is_generacion(self) -> None:
        assert self._state(23, 0) == "GENERACION"

    def test_2355_watchdog_time_is_generacion(self) -> None:
        assert self._state(23, 55) == "GENERACION"

    def test_2359_is_generacion(self) -> None:
        assert self._state(23, 59) == "GENERACION"


# ---------------------------------------------------------------------------
# next_n_ticks
# ---------------------------------------------------------------------------


class TestNextNTicks:
    def test_slots_are_chronological(self) -> None:
        """TICK_SLOTS are ordered by ART time."""
        times = [s.art_local_time for s in TICK_SLOTS]
        assert times == sorted(times)

    def test_four_defined_ticks(self) -> None:
        assert len(TICK_SLOTS) == 4
        names = [s.tick for s in TICK_SLOTS]
        assert names == ["ESTRENO", "FILTERING", "GENERACION", "WATCHDOG"]

    def test_returns_n_instances(self) -> None:
        now = _utc(2026, 6, 9, 10, 0)  # 07:00 ART — before any tick today
        ticks = next_n_ticks(now, 4)
        assert len(ticks) == 4

    def test_all_strictly_after_now(self) -> None:
        now = _utc(2026, 6, 9, 10, 0)
        for tick in next_n_ticks(now, 8):
            assert tick.fires_at_utc > now

    def test_results_are_chronological(self) -> None:
        now = _utc(2026, 6, 9, 10, 0)
        ticks = next_n_ticks(now, 8)
        for a, b in itertools.pairwise(ticks):
            assert a.fires_at_utc < b.fires_at_utc

    def test_returns_schedule_slot_instances(self) -> None:
        ticks = next_n_ticks(_utc(2026, 6, 9, 10, 0), 1)
        assert isinstance(ticks[0], ScheduleSlotInstance)

    def test_estreno_fires_at_1500_utc(self) -> None:
        """12:00 ART ESTRENO tick = 15:00 UTC (UTC+3 → UTC)."""
        # At ART midnight (UTC 03:00), the next tick is today's ESTRENO at 15:00 UTC.
        now = _utc(2026, 6, 10, 3, 0)  # ART 2026-06-10 00:00
        ticks = next_n_ticks(now, 1)
        assert ticks[0].slot.tick == "ESTRENO"
        assert ticks[0].fires_at_utc == _utc(2026, 6, 10, 15, 0)

    def test_skips_past_ticks_current_day(self) -> None:
        """If it is 20:00 ART, only GENERACION and WATCHDOG remain today."""
        # 20:00 ART = 23:00 UTC
        now = _utc(2026, 6, 9, 23, 0)
        ticks = next_n_ticks(now, 2)
        names = [t.slot.tick for t in ticks]
        assert names == ["GENERACION", "WATCHDOG"]

    def test_after_watchdog_rolls_to_next_day(self) -> None:
        """After 23:55 ART, all four ticks are from tomorrow."""
        # 23:56 ART = 02:56 UTC next day
        now = _utc(2026, 6, 10, 2, 56)  # ART 2026-06-09 23:56
        ticks = next_n_ticks(now, 4)
        names = [t.slot.tick for t in ticks]
        assert names == ["ESTRENO", "FILTERING", "GENERACION", "WATCHDOG"]
        # All should fire on UTC 2026-06-10 (ART 2026-06-10)
        assert all(t.fires_at_utc.date().isoformat() >= "2026-06-10" for t in ticks)

    def test_n_zero_returns_empty(self) -> None:
        ticks = next_n_ticks(_utc(2026, 6, 9, 12, 0), 0)
        assert ticks == []

    def test_naive_utc_input_accepted(self) -> None:
        """Naive datetime is treated as UTC."""
        naive_now = datetime(2026, 6, 9, 10, 0)  # no tzinfo
        ticks = next_n_ticks(naive_now, 1)
        assert len(ticks) == 1
        assert ticks[0].fires_at_utc.tzinfo is not None  # output is always tz-aware
