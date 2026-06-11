"""Unit tests: QuotaState value object.

Module 005 / Task T-003.

Coverage:
  - ``remaining`` = max - used, clamped to 0
  - ``at_capacity`` True iff used >= max
  - Frozen: assigning attrs raises FrozenInstanceError
  - Equality across identical (used, max)
  - Hashable: usable as dict key / set member
"""

from __future__ import annotations

import dataclasses

import pytest

from app.domain.twist_quota import QuotaState

# ---------------------------------------------------------------------------
# remaining
# ---------------------------------------------------------------------------


def test_remaining_empty_state() -> None:
    assert QuotaState(used=0, max=3).remaining == 3


def test_remaining_partial() -> None:
    assert QuotaState(used=1, max=3).remaining == 2


def test_remaining_zero_when_exact_capacity() -> None:
    assert QuotaState(used=3, max=3).remaining == 0


def test_remaining_clamped_when_over_capacity() -> None:
    # Theoretically unreachable thanks to the advisory lock, but defensive
    # against bugs: never surface a negative remaining to clients.
    assert QuotaState(used=4, max=3).remaining == 0


def test_remaining_zero_when_max_is_zero() -> None:
    assert QuotaState(used=0, max=0).remaining == 0


# ---------------------------------------------------------------------------
# at_capacity
# ---------------------------------------------------------------------------


def test_at_capacity_false_when_room_left() -> None:
    assert QuotaState(used=2, max=3).at_capacity is False


def test_at_capacity_true_when_exact() -> None:
    assert QuotaState(used=3, max=3).at_capacity is True


def test_at_capacity_true_when_over() -> None:
    assert QuotaState(used=5, max=3).at_capacity is True


def test_at_capacity_true_when_max_is_zero() -> None:
    assert QuotaState(used=0, max=0).at_capacity is True


# ---------------------------------------------------------------------------
# Frozen, equality, hashable
# ---------------------------------------------------------------------------


def test_frozen_cannot_assign() -> None:
    state = QuotaState(used=1, max=3)
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.used = 2  # type: ignore[misc]


def test_equality_by_value() -> None:
    assert QuotaState(used=1, max=3) == QuotaState(used=1, max=3)


def test_inequality_different_used() -> None:
    assert QuotaState(used=1, max=3) != QuotaState(used=2, max=3)


def test_hashable_in_set() -> None:
    a = QuotaState(used=1, max=3)
    b = QuotaState(used=1, max=3)
    c = QuotaState(used=2, max=3)
    assert {a, b, c} == {a, c}
