"""Per-user stable sort + tiebreak rules for the vote-feed.

Module 007 / Task T-002.

Spec FR-002: default sort is ``random`` with seed
``sha256(f"{cycle_id}:{user_id}")[:8]`` interpreted as int. Optional
``recent`` (``submitted_at DESC``) and ``hot`` (``vote_count DESC``,
tiebreak ``submitted_at ASC``).

Pure: no DB, no HTTP, no time. Items are passed in already projected to
the minimal shape ``HasSortKeys`` so this module stays config-free.
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime
from typing import Protocol, TypeVar


class HasSortKeys(Protocol):
    """Minimal item shape for vote-feed sorting."""

    submitted_at: datetime
    vote_count: int


T = TypeVar("T", bound=HasSortKeys)


def seed_int(cycle_id: int, user_id: int) -> int:
    """Deterministic int seed from (cycle_id, user_id).

    Uses the first 8 bytes of ``sha256(f"{cycle_id}:{user_id}")`` as a
    big-endian unsigned 64-bit int. The same (cycle, user) always yields
    the same seed; different (cycle, user) yields uniformly distributed
    seeds.
    """
    digest = hashlib.sha256(f"{cycle_id}:{user_id}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


def shuffle_stable(items: list[T], *, cycle_id: int, user_id: int) -> list[T]:
    """Return ``items`` shuffled deterministically per (cycle, user).

    Two calls with the same (cycle_id, user_id, items) yield the same order.
    Different users see different orders even within the same cycle, which
    is the anti-refresh-gaming property of FR-002.
    """
    rng = random.Random(seed_int(cycle_id, user_id))
    shuffled = list(items)
    rng.shuffle(shuffled)
    return shuffled


def sort_recent(items: list[T]) -> list[T]:
    """Sort by ``submitted_at DESC``. Stable on ties."""
    return sorted(items, key=lambda i: i.submitted_at, reverse=True)


def sort_hot(items: list[T]) -> list[T]:
    """Sort by ``vote_count DESC``, tiebreak ``submitted_at ASC``.

    Older twists win ties so newcomers don't displace established
    favorites at the top of the feed.
    """
    return sorted(items, key=lambda i: (-i.vote_count, i.submitted_at))
