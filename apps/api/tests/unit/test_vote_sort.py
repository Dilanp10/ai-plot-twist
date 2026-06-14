"""Unit tests: vote_sort.

Module 007 / Task T-002.

Coverage:
  - ``seed_int`` is deterministic per (cycle_id, user_id).
  - ``seed_int`` differs across different (cycle_id, user_id) pairs.
  - ``shuffle_stable`` is deterministic on identical inputs (100 trials).
  - ``shuffle_stable`` produces statistically different orders across users.
  - ``sort_recent`` sorts by ``submitted_at DESC``.
  - ``sort_hot`` sorts by ``vote_count DESC`` with ``submitted_at ASC`` tiebreak.
  - Empty / single-item lists are no-ops for all sorts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.domain.vote_sort import (
    seed_int,
    shuffle_stable,
    sort_hot,
    sort_recent,
)


@dataclass
class _Item:
    """Test double matching the ``HasSortKeys`` protocol."""

    id: int
    submitted_at: datetime
    vote_count: int


_BASE = datetime(2026, 6, 13, 12, 0, 0, tzinfo=UTC)


def _items(n: int) -> list[_Item]:
    return [
        _Item(id=i, submitted_at=_BASE + timedelta(minutes=i), vote_count=i)
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# seed_int
# ---------------------------------------------------------------------------


def test_seed_int_deterministic() -> None:
    """Same input → same seed across calls."""
    assert seed_int(7, 42) == seed_int(7, 42)


def test_seed_int_differs_by_cycle() -> None:
    """Different cycle, same user → different seed."""
    assert seed_int(7, 42) != seed_int(8, 42)


def test_seed_int_differs_by_user() -> None:
    """Same cycle, different user → different seed."""
    assert seed_int(7, 42) != seed_int(7, 43)


def test_seed_int_fits_unsigned_64bit() -> None:
    """Seed is a non-negative int that fits in 64 bits (first 8 bytes of sha256)."""
    s = seed_int(123_456, 999)
    assert 0 <= s < 2**64


# ---------------------------------------------------------------------------
# shuffle_stable
# ---------------------------------------------------------------------------


def test_shuffle_stable_same_seed_same_order_100_trials() -> None:
    """100 calls with identical inputs return identical orderings (FR-002 / Gate 5)."""
    items = _items(20)
    expected = shuffle_stable(items, cycle_id=7, user_id=42)
    for _ in range(100):
        assert shuffle_stable(items, cycle_id=7, user_id=42) == expected


def test_shuffle_stable_different_users_different_orders() -> None:
    """Statistically, different users see different orders across a cohort."""
    items = _items(30)
    orders: set[tuple[int, ...]] = set()
    for user_id in range(50):
        order = tuple(i.id for i in shuffle_stable(items, cycle_id=7, user_id=user_id))
        orders.add(order)
    # 50 distinct seeds against a 30-item permutation space should yield
    # essentially 50 distinct orderings. Allow tiny slack for hash collisions.
    assert len(orders) >= 45


def test_shuffle_stable_preserves_items_set() -> None:
    """Output is a permutation: same multiset as input."""
    items = _items(15)
    shuffled = shuffle_stable(items, cycle_id=7, user_id=42)
    assert sorted(i.id for i in shuffled) == sorted(i.id for i in items)


def test_shuffle_stable_does_not_mutate_input() -> None:
    """The caller's list is not modified in place."""
    items = _items(10)
    snapshot = [i.id for i in items]
    shuffle_stable(items, cycle_id=7, user_id=42)
    assert [i.id for i in items] == snapshot


def test_shuffle_stable_empty_list() -> None:
    assert shuffle_stable([], cycle_id=1, user_id=1) == []


def test_shuffle_stable_single_item() -> None:
    items = _items(1)
    assert shuffle_stable(items, cycle_id=1, user_id=1) == items


# ---------------------------------------------------------------------------
# sort_recent
# ---------------------------------------------------------------------------


def test_sort_recent_orders_desc_by_submitted_at() -> None:
    items = _items(5)  # i=0..4, submitted_at increasing
    out = sort_recent(items)
    assert [i.id for i in out] == [4, 3, 2, 1, 0]


def test_sort_recent_stable_on_tie() -> None:
    """Same ``submitted_at`` preserves original input order (Python's sort is stable)."""
    same_ts = _BASE
    items = [
        _Item(id=1, submitted_at=same_ts, vote_count=0),
        _Item(id=2, submitted_at=same_ts, vote_count=0),
        _Item(id=3, submitted_at=same_ts, vote_count=0),
    ]
    out = sort_recent(items)
    assert [i.id for i in out] == [1, 2, 3]


def test_sort_recent_empty() -> None:
    assert sort_recent([]) == []


# ---------------------------------------------------------------------------
# sort_hot
# ---------------------------------------------------------------------------


def test_sort_hot_orders_by_vote_count_desc() -> None:
    items = [
        _Item(id=1, submitted_at=_BASE, vote_count=1),
        _Item(id=2, submitted_at=_BASE, vote_count=5),
        _Item(id=3, submitted_at=_BASE, vote_count=3),
    ]
    out = sort_hot(items)
    assert [i.id for i in out] == [2, 3, 1]


def test_sort_hot_tiebreak_submitted_at_asc() -> None:
    """Same vote_count → older twist (lower submitted_at) wins."""
    items = [
        _Item(id=1, submitted_at=_BASE + timedelta(hours=2), vote_count=3),
        _Item(id=2, submitted_at=_BASE, vote_count=3),  # oldest
        _Item(id=3, submitted_at=_BASE + timedelta(hours=1), vote_count=3),
    ]
    out = sort_hot(items)
    assert [i.id for i in out] == [2, 3, 1]


def test_sort_hot_combined_ordering() -> None:
    """Vote count dominates; tiebreak only kicks in within a count group."""
    items = [
        _Item(id=1, submitted_at=_BASE, vote_count=2),
        _Item(id=2, submitted_at=_BASE + timedelta(hours=1), vote_count=5),
        _Item(id=3, submitted_at=_BASE, vote_count=5),  # ties with id=2, older
        _Item(id=4, submitted_at=_BASE + timedelta(hours=1), vote_count=2),  # ties with id=1, newer
    ]
    out = sort_hot(items)
    assert [i.id for i in out] == [3, 2, 1, 4]


def test_sort_hot_empty() -> None:
    assert sort_hot([]) == []
