"""Unit tests: cycle_fsm pure FSM.

Module 003 / Task T-005.

Exhaustive coverage:
  - All 7 x 7 = 49 (from, to) pairs classified as legal or illegal
    (with skip_dwell=True to decouple from time).
  - TimeFenceViolation raised for each state that has min_dwell > 0
    when elapsed time is too short.
  - skip_dwell=True bypasses time fence on legal transitions.
  - TransitionPlan carries correct side_effect and state_updates.
  - compute() is deterministic (same inputs → identical plan).
  - IllegalTransition / TimeFenceViolation carry useful attributes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.cycle_fsm import (
    ALL_STATES,
    MIN_DWELL_SECONDS,
    IllegalTransition,
    TimeFenceViolation,
    TransitionPlan,
    compute,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STATES = sorted(ALL_STATES)  # deterministic order for parametrize

# Legal edges (from_state, to_state)
_LEGAL: frozenset[tuple[str, str]] = frozenset(
    {
        ("PENDING_RELEASE", "ESTRENO"),
        ("ESTRENO", "RECEPCION_IDEAS"),
        ("RECEPCION_IDEAS", "FILTERING"),
        ("FILTERING", "VOTACION"),
        ("FILTERING", "FAILED"),
        ("VOTACION", "GENERACION"),
        ("GENERACION", "PENDING_RELEASE"),
        ("GENERACION", "FAILED"),
    }
)


def _now() -> datetime:
    return datetime(2026, 6, 9, 20, 0, 0, tzinfo=UTC)


def _entered_long_ago(state: str) -> datetime:
    """Return a state_entered_at that satisfies min_dwell for *state*."""
    dwell = MIN_DWELL_SECONDS.get(state, 0)
    # Extra margin: dwell + 1 hour
    return _now() - timedelta(seconds=dwell + 3600)


def _entered_just_now() -> datetime:
    """Return a state_entered_at that is 0 s ago (dwell always violated)."""
    return _now()


# ---------------------------------------------------------------------------
# Exhaustive legal/illegal classification (49 combinations)
# ---------------------------------------------------------------------------


class TestExhaustivePairs:
    @pytest.mark.parametrize(
        "from_state,to_state",
        [(f, t) for f in _STATES for t in _STATES],
    )
    def test_legal_vs_illegal(self, from_state: str, to_state: str) -> None:
        """Every (from, to) pair is either legal or raises IllegalTransition."""
        entered = _entered_long_ago(from_state)
        now = _now()

        if (from_state, to_state) in _LEGAL:
            plan = compute(from_state, to_state, entered, now, skip_dwell=True)
            assert plan.to == to_state
            assert isinstance(plan, TransitionPlan)
        else:
            with pytest.raises(IllegalTransition) as exc_info:
                compute(from_state, to_state, entered, now, skip_dwell=True)
            err = exc_info.value
            assert err.from_state == from_state
            assert err.to_state == to_state


# ---------------------------------------------------------------------------
# Legal transitions — happy path (skip_dwell=True)
# ---------------------------------------------------------------------------


class TestLegalTransitions:
    def _plan(
        self, from_state: str, to_state: str, *, skip_dwell: bool = True
    ) -> TransitionPlan:
        entered = _entered_long_ago(from_state)
        return compute(from_state, to_state, entered, _now(), skip_dwell=skip_dwell)

    def test_pending_release_to_estreno(self) -> None:
        plan = self._plan("PENDING_RELEASE", "ESTRENO")
        assert plan.to == "ESTRENO"
        assert plan.side_effect is None
        assert plan.state_updates["chapter_status"] == "live"
        assert plan.state_updates["chapter_released_at"] == "now"

    def test_estreno_to_recepcion_ideas(self) -> None:
        plan = self._plan("ESTRENO", "RECEPCION_IDEAS")
        assert plan.to == "RECEPCION_IDEAS"
        assert plan.side_effect is None

    def test_recepcion_ideas_to_filtering(self) -> None:
        plan = self._plan("RECEPCION_IDEAS", "FILTERING")
        assert plan.to == "FILTERING"
        assert plan.side_effect == "director_filter"

    def test_filtering_to_votacion(self) -> None:
        plan = self._plan("FILTERING", "VOTACION")
        assert plan.to == "VOTACION"
        assert plan.side_effect is None

    def test_filtering_to_failed(self) -> None:
        plan = self._plan("FILTERING", "FAILED")
        assert plan.to == "FAILED"
        assert plan.side_effect is None

    def test_votacion_to_generacion(self) -> None:
        plan = self._plan("VOTACION", "GENERACION")
        assert plan.to == "GENERACION"
        assert plan.side_effect == "generation_pipeline"

    def test_generacion_to_pending_release(self) -> None:
        plan = self._plan("GENERACION", "PENDING_RELEASE")
        assert plan.to == "PENDING_RELEASE"
        assert plan.side_effect is None

    def test_generacion_to_failed(self) -> None:
        plan = self._plan("GENERACION", "FAILED")
        assert plan.to == "FAILED"
        assert plan.side_effect is None


# ---------------------------------------------------------------------------
# Illegal transitions — explicit spot-checks
# ---------------------------------------------------------------------------


class TestIllegalTransitions:
    def _assert_illegal(self, from_state: str, to_state: str) -> IllegalTransition:
        entered = _entered_long_ago(from_state)
        with pytest.raises(IllegalTransition) as exc_info:
            compute(from_state, to_state, entered, _now(), skip_dwell=True)
        return exc_info.value

    def test_votacion_to_estreno(self) -> None:
        """Spec acceptance scenario: illegal jump VOTACION→ESTRENO."""
        err = self._assert_illegal("VOTACION", "ESTRENO")
        assert err.from_state == "VOTACION"
        assert err.to_state == "ESTRENO"

    def test_pending_release_to_filtering(self) -> None:
        self._assert_illegal("PENDING_RELEASE", "FILTERING")

    def test_pending_release_to_generacion(self) -> None:
        self._assert_illegal("PENDING_RELEASE", "GENERACION")

    def test_estreno_to_generacion(self) -> None:
        self._assert_illegal("ESTRENO", "GENERACION")

    def test_failed_to_pending_release(self) -> None:
        """FAILED is terminal — no out-edges in the regular table."""
        self._assert_illegal("FAILED", "PENDING_RELEASE")

    def test_failed_to_estreno(self) -> None:
        self._assert_illegal("FAILED", "ESTRENO")

    def test_self_loop_pending_release(self) -> None:
        self._assert_illegal("PENDING_RELEASE", "PENDING_RELEASE")

    def test_self_loop_generacion(self) -> None:
        self._assert_illegal("GENERACION", "GENERACION")

    def test_recepcion_ideas_to_votacion_skip(self) -> None:
        """Skipping a state is illegal."""
        self._assert_illegal("RECEPCION_IDEAS", "VOTACION")

    def test_illegal_transition_str(self) -> None:
        err = self._assert_illegal("VOTACION", "ESTRENO")
        assert "VOTACION" in str(err)
        assert "ESTRENO" in str(err)


# ---------------------------------------------------------------------------
# Time-fence (min_dwell)
# ---------------------------------------------------------------------------


class TestTimeFence:
    """FR-005: transitions before min_dwell raise TimeFenceViolation."""

    def _compute_no_skip(
        self,
        from_state: str,
        to_state: str,
        elapsed_s: float,
    ) -> TransitionPlan:
        now = _now()
        entered = now - timedelta(seconds=elapsed_s)
        return compute(from_state, to_state, entered, now, skip_dwell=False)

    def _assert_fence(
        self,
        from_state: str,
        to_state: str,
        elapsed_s: float,
    ) -> TimeFenceViolation:
        with pytest.raises(TimeFenceViolation) as exc_info:
            self._compute_no_skip(from_state, to_state, elapsed_s)
        return exc_info.value

    # ESTRENO → RECEPCION_IDEAS (min 60 s)
    def test_estreno_min_dwell_violated(self) -> None:
        err = self._assert_fence("ESTRENO", "RECEPCION_IDEAS", elapsed_s=30)
        assert err.from_state == "ESTRENO"
        assert err.to_state == "RECEPCION_IDEAS"
        assert err.min_dwell_s == 60
        assert err.elapsed_s == pytest.approx(30, abs=1)

    def test_estreno_min_dwell_exact_boundary(self) -> None:
        """60 s elapsed is on the boundary — should pass."""
        plan = self._compute_no_skip("ESTRENO", "RECEPCION_IDEAS", elapsed_s=60)
        assert plan.to == "RECEPCION_IDEAS"

    def test_estreno_min_dwell_just_over(self) -> None:
        plan = self._compute_no_skip("ESTRENO", "RECEPCION_IDEAS", elapsed_s=61)
        assert plan.to == "RECEPCION_IDEAS"

    # RECEPCION_IDEAS → FILTERING (min 19 800 s = 5 h 30 min)
    def test_recepcion_ideas_fence_too_early(self) -> None:
        err = self._assert_fence("RECEPCION_IDEAS", "FILTERING", elapsed_s=100)
        assert err.min_dwell_s == 19_800

    def test_recepcion_ideas_fence_passed(self) -> None:
        plan = self._compute_no_skip("RECEPCION_IDEAS", "FILTERING", elapsed_s=19_801)
        assert plan.to == "FILTERING"

    # FILTERING → VOTACION (min 1 s)
    def test_filtering_fence_zero_elapsed(self) -> None:
        err = self._assert_fence("FILTERING", "VOTACION", elapsed_s=0)
        assert err.min_dwell_s == 1

    def test_filtering_fence_1s_passed(self) -> None:
        plan = self._compute_no_skip("FILTERING", "VOTACION", elapsed_s=1)
        assert plan.to == "VOTACION"

    # VOTACION → GENERACION (min 17 100 s = 4 h 45 min)
    def test_votacion_fence_too_early(self) -> None:
        err = self._assert_fence("VOTACION", "GENERACION", elapsed_s=1000)
        assert err.min_dwell_s == 17_100

    def test_votacion_fence_passed(self) -> None:
        plan = self._compute_no_skip("VOTACION", "GENERACION", elapsed_s=17_101)
        assert plan.to == "GENERACION"

    # GENERACION → PENDING_RELEASE (min 1 800 s = 30 min)
    def test_generacion_fence_too_early(self) -> None:
        err = self._assert_fence("GENERACION", "PENDING_RELEASE", elapsed_s=60)
        assert err.min_dwell_s == 1_800

    def test_generacion_fence_passed(self) -> None:
        plan = self._compute_no_skip("GENERACION", "PENDING_RELEASE", elapsed_s=1_801)
        assert plan.to == "PENDING_RELEASE"

    # PENDING_RELEASE → ESTRENO (min 0 s — always passes)
    def test_pending_release_no_fence(self) -> None:
        """min_dwell = 0 → transition passes even with 0 s elapsed."""
        plan = self._compute_no_skip("PENDING_RELEASE", "ESTRENO", elapsed_s=0)
        assert plan.to == "ESTRENO"

    def test_fence_carries_earliest_at(self) -> None:
        """earliest_at is exactly state_entered_at + min_dwell."""
        now = _now()
        entered = now - timedelta(seconds=30)  # only 30 s in ESTRENO
        with pytest.raises(TimeFenceViolation) as exc_info:
            compute("ESTRENO", "RECEPCION_IDEAS", entered, now, skip_dwell=False)
        err = exc_info.value
        expected_earliest = entered + timedelta(seconds=60)
        assert err.earliest_at == expected_earliest

    def test_fence_str_contains_details(self) -> None:
        err = self._assert_fence("ESTRENO", "RECEPCION_IDEAS", elapsed_s=5)
        s = str(err)
        assert "ESTRENO" in s
        assert "RECEPCION_IDEAS" in s
        assert "60" in s  # min_dwell_s


# ---------------------------------------------------------------------------
# skip_dwell bypasses time fence
# ---------------------------------------------------------------------------


class TestSkipDwell:
    def test_skip_dwell_bypasses_estreno_fence(self) -> None:
        now = _now()
        entered = now  # 0 s elapsed — would normally violate 60 s fence
        plan = compute(
            "ESTRENO", "RECEPCION_IDEAS", entered, now, skip_dwell=True
        )
        assert plan.to == "RECEPCION_IDEAS"

    def test_skip_dwell_bypasses_votacion_fence(self) -> None:
        now = _now()
        entered = now  # 0 s elapsed — would normally violate 4 h 45 min fence
        plan = compute(
            "VOTACION", "GENERACION", entered, now, skip_dwell=True
        )
        assert plan.to == "GENERACION"

    def test_skip_dwell_does_not_bypass_illegal_transition(self) -> None:
        """skip_dwell only bypasses time fence, not legal-transition check."""
        entered = _entered_long_ago("VOTACION")
        with pytest.raises(IllegalTransition):
            compute("VOTACION", "ESTRENO", entered, _now(), skip_dwell=True)

    def test_skip_dwell_false_by_default(self) -> None:
        """Default skip_dwell=False — time fence is active."""
        now = _now()
        entered = now  # 0 s in ESTRENO
        with pytest.raises(TimeFenceViolation):
            compute("ESTRENO", "RECEPCION_IDEAS", entered, now)


# ---------------------------------------------------------------------------
# Naive datetime handling
# ---------------------------------------------------------------------------


class TestNaiveDatetimes:
    def test_naive_state_entered_at_treated_as_utc(self) -> None:
        """Naive state_entered_at is assumed UTC."""
        now = _now()
        naive_entered = datetime(2026, 6, 9, 18, 0, 0)  # no tzinfo
        plan = compute(
            "PENDING_RELEASE", "ESTRENO", naive_entered, now, skip_dwell=True
        )
        assert plan.to == "ESTRENO"

    def test_naive_now_utc_treated_as_utc(self) -> None:
        naive_now = datetime(2026, 6, 9, 20, 0, 0)  # no tzinfo
        entered = datetime(2026, 6, 9, 10, 0, 0, tzinfo=UTC)  # 10 h ago
        plan = compute(
            "PENDING_RELEASE", "ESTRENO", entered, naive_now, skip_dwell=False
        )
        assert plan.to == "ESTRENO"


# ---------------------------------------------------------------------------
# TransitionPlan content
# ---------------------------------------------------------------------------


class TestTransitionPlanContent:
    def test_side_effect_only_on_filtering_and_generacion_entries(self) -> None:
        """director_filter and generation_pipeline are the only side effects."""
        non_effect_transitions = [
            ("PENDING_RELEASE", "ESTRENO"),
            ("ESTRENO", "RECEPCION_IDEAS"),
            ("FILTERING", "VOTACION"),
            ("FILTERING", "FAILED"),
            ("GENERACION", "PENDING_RELEASE"),
            ("GENERACION", "FAILED"),
        ]
        for from_s, to_s in non_effect_transitions:
            entered = _entered_long_ago(from_s)
            plan = compute(from_s, to_s, entered, _now(), skip_dwell=True)
            assert plan.side_effect is None, (
                f"Expected no side effect for {from_s}→{to_s}, got {plan.side_effect!r}"
            )

    def test_director_filter_only_on_recepcion_ideas_to_filtering(self) -> None:
        entered = _entered_long_ago("RECEPCION_IDEAS")
        plan = compute("RECEPCION_IDEAS", "FILTERING", entered, _now(), skip_dwell=True)
        assert plan.side_effect == "director_filter"

    def test_generation_pipeline_only_on_votacion_to_generacion(self) -> None:
        entered = _entered_long_ago("VOTACION")
        plan = compute("VOTACION", "GENERACION", entered, _now(), skip_dwell=True)
        assert plan.side_effect == "generation_pipeline"

    def test_state_updates_chapter_fields_on_estreno(self) -> None:
        entered = _entered_long_ago("PENDING_RELEASE")
        plan = compute("PENDING_RELEASE", "ESTRENO", entered, _now(), skip_dwell=True)
        assert plan.state_updates == {
            "chapter_status": "live",
            "chapter_released_at": "now",
        }

    def test_state_updates_empty_for_most_transitions(self) -> None:
        transitions_with_empty_updates = [
            ("ESTRENO", "RECEPCION_IDEAS"),
            ("FILTERING", "VOTACION"),
            ("FILTERING", "FAILED"),
            ("VOTACION", "GENERACION"),
            ("GENERACION", "PENDING_RELEASE"),
            ("GENERACION", "FAILED"),
        ]
        for from_s, to_s in transitions_with_empty_updates:
            entered = _entered_long_ago(from_s)
            plan = compute(from_s, to_s, entered, _now(), skip_dwell=True)
            assert plan.state_updates == {}, (
                f"Expected empty state_updates for {from_s}→{to_s}"
            )


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_inputs_produce_identical_plan(self) -> None:
        """compute() is deterministic — same inputs → identical result."""
        entered = _entered_long_ago("RECEPCION_IDEAS")
        now = _now()

        plan_a = compute(
            "RECEPCION_IDEAS", "FILTERING", entered, now, skip_dwell=True
        )
        plan_b = compute(
            "RECEPCION_IDEAS", "FILTERING", entered, now, skip_dwell=True
        )

        assert plan_a.to == plan_b.to
        assert plan_a.side_effect == plan_b.side_effect
        assert plan_a.state_updates == plan_b.state_updates

    def test_all_legal_edges_deterministic(self) -> None:
        """All 8 legal edges produce consistent plans across two calls."""
        legal_list = list(_LEGAL)
        for from_s, to_s in legal_list:
            entered = _entered_long_ago(from_s)
            now = _now()
            p1 = compute(from_s, to_s, entered, now, skip_dwell=True)
            p2 = compute(from_s, to_s, entered, now, skip_dwell=True)
            assert p1 == p2, f"Non-deterministic result for {from_s}→{to_s}"
