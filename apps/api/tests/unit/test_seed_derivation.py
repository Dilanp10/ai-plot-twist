"""Unit tests: seed_derivation.

Module 008 / Task T-002.

All tests are pure Python — no database, no I/O.

Coverage:
  - Determinism: same (chapter_id, panel_idx) → same seed across 100 calls.
  - Uniqueness by chapter: different chapter_id → different seed.
  - Uniqueness by panel: different panel_idx → different seed.
  - Range: result fits in [0, 2**32 - 1] (unsigned 32-bit).
  - Panel coverage: all panel indices 1..4 produce distinct seeds for a
    given chapter.
"""

from __future__ import annotations

from app.domain.seed_derivation import stable_hash

_CHAPTER = 42
_PANEL = 2


def test_deterministic_across_calls() -> None:
    """100 calls with identical inputs return identical seeds."""
    expected = stable_hash(_CHAPTER, _PANEL)
    for _ in range(100):
        assert stable_hash(_CHAPTER, _PANEL) == expected


def test_differs_by_chapter_id() -> None:
    """Different chapter_id → different seed."""
    assert stable_hash(1, _PANEL) != stable_hash(2, _PANEL)


def test_differs_by_panel_idx() -> None:
    """Different panel_idx → different seed."""
    assert stable_hash(_CHAPTER, 1) != stable_hash(_CHAPTER, 2)


def test_fits_unsigned_32bit() -> None:
    """Seed is in [0, 2**32 - 1]."""
    value = stable_hash(_CHAPTER, _PANEL)
    assert 0 <= value <= 2**32 - 1


def test_non_negative() -> None:
    """Seed is never negative."""
    for chapter_id in range(10):
        for panel_idx in range(1, 5):
            assert stable_hash(chapter_id, panel_idx) >= 0


def test_all_panels_distinct_for_chapter() -> None:
    """Panel indices 1..4 produce four distinct seeds for the same chapter."""
    seeds = [stable_hash(_CHAPTER, idx) for idx in range(1, 5)]
    assert len(set(seeds)) == 4


def test_stable_across_chapters() -> None:
    """Same (chapter_id, panel_idx) always returns the same value — spot check."""
    assert stable_hash(1, 1) == stable_hash(1, 1)
    assert stable_hash(999, 4) == stable_hash(999, 4)
    assert stable_hash(0, 0) == stable_hash(0, 0)
