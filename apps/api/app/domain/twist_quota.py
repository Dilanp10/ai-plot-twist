"""Twist quota state.

Module 005 / Task T-003.

Pure value object representing how many twists a user has submitted for a
given chapter, regardless of status. The max is supplied by the caller
(typically settings.MAX_TWISTS_PER_USER_PER_CHAPTER) so this module stays
config-free and trivially unit-testable.

A frozen dataclass — instances are hashable and safe to compare for
equality. ``remaining`` clamps at 0 so over-capacity states (theoretically
unreachable thanks to the advisory lock in T-005, but possible under a
bug) never surface negative numbers to clients.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QuotaState:
    """Immutable snapshot of (used, max) twists for a (user, chapter) pair."""

    used: int
    max: int

    @property
    def remaining(self) -> int:
        """Twists the user can still submit. Clamped to 0."""
        diff = self.max - self.used
        return diff if diff > 0 else 0

    @property
    def at_capacity(self) -> bool:
        """True when the user cannot submit more twists."""
        return self.used >= self.max
