"""VotesRepo — SQLAlchemy Core repository for the ``votes`` table.

Module 007 / Task T-004.

All methods operate on the caller-supplied ``AsyncSession``; the caller
is responsible for committing or rolling back.

The two race-safety primitives are intentionally separated:

* :meth:`lock_user_chapter` acquires ``pg_advisory_xact_lock`` keyed on
  ``vote_quota:<user_id>:<chapter_id>`` with a 1-second timeout. The
  service holds it across the *count + insert* pair so two concurrent
  votes from the same user against different twists cannot both observe
  the quota as available (FR-006 / R-V1).

* :meth:`vote_atomic` issues ``INSERT … ON CONFLICT (twist_id, user_id)
  DO NOTHING RETURNING id`` and returns the new row id, or *None* when
  the UNIQUE constraint absorbed the second tap. That is the natural
  idempotency anchor for the same-twist double-tap race (FR-005 / R-V4):
  the second insert affects 0 rows, the service returns 409
  ``already_voted``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession


class VoteLockBusy(Exception):
    """Raised when the per-(user, chapter) advisory lock cannot be
    acquired within 1 s.

    The vote service should translate this to HTTP 503 with
    ``{"code": "lock_busy"}``.
    """

    def __init__(self, user_id: int, chapter_id: int) -> None:
        self.user_id = user_id
        self.chapter_id = chapter_id
        super().__init__(
            f"Advisory lock for vote_quota:{user_id}:{chapter_id} "
            f"could not be acquired within 1 s"
        )


@dataclass
class Vote:
    """Flat projection of a ``votes`` row."""

    id: int
    twist_id: int
    user_id: int
    chapter_id: int
    created_at: datetime


def _map_row(row: Any) -> Vote:
    return Vote(
        id=int(row["id"]),
        twist_id=int(row["twist_id"]),
        user_id=int(row["user_id"]),
        chapter_id=int(row["chapter_id"]),
        created_at=row["created_at"],
    )


_SELECT_COLS = "id, twist_id, user_id, chapter_id, created_at"


@dataclass(frozen=True)
class FeedRow:
    """One ``approved`` twist plus its current ``vote_count``.

    Used by the vote-feed read path. The ``id`` field is the *internal*
    integer id (needed to project ``has_my_vote`` cheaply against
    ``list_for_user_chapter``); ``public_id`` is what is surfaced over
    the wire.
    """

    id: int
    public_id: UUID
    content: str
    vote_count: int
    submitted_at: datetime


class VotesRepo:
    """Repository for the ``votes`` table.

    Parameters
    ----------
    session:
        Active ``AsyncSession``. Caller manages commit/rollback.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def count_for_user_chapter(
        self, user_id: int, chapter_id: int
    ) -> int:
        """Return vote count for (user, chapter).

        Hits ``idx_votes_user_chapter`` (migration 0008). Used by the
        quota check inside the advisory-locked critical section.
        """
        result = await self._s.execute(
            sa.text(
                "SELECT COUNT(*) FROM votes "
                "WHERE user_id = :user_id AND chapter_id = :chapter_id"
            ),
            {"user_id": user_id, "chapter_id": chapter_id},
        )
        return int(result.scalar_one())

    async def list_for_user_chapter(
        self, user_id: int, chapter_id: int
    ) -> list[Vote]:
        """Return every vote this user has cast in this chapter.

        Bounded by the quota (≤ 5 rows), so no pagination is needed.
        Used to project ``has_my_vote`` over the vote-feed (FR-004).
        """
        result = await self._s.execute(
            sa.text(
                f"SELECT {_SELECT_COLS} FROM votes "
                "WHERE user_id = :user_id AND chapter_id = :chapter_id "
                "ORDER BY created_at ASC"
            ),
            {"user_id": user_id, "chapter_id": chapter_id},
        )
        return [_map_row(row) for row in result.mappings()]

    async def count_for_twist(self, twist_id: int) -> int:
        """Return total votes for a twist.

        Hits ``idx_votes_twist``. Used to fill ``new_vote_count`` in the
        cast response (FR-007).
        """
        result = await self._s.execute(
            sa.text("SELECT COUNT(*) FROM votes WHERE twist_id = :twist_id"),
            {"twist_id": twist_id},
        )
        return int(result.scalar_one())

    async def vote_atomic(
        self, twist_id: int, user_id: int, chapter_id: int
    ) -> int | None:
        """Insert a vote with ``ON CONFLICT (twist_id, user_id) DO NOTHING``.

        Returns the new row id on success, or *None* when the UNIQUE
        constraint absorbed a duplicate. The caller maps *None* to 409
        ``already_voted`` (FR-005).

        ``chapter_id`` is written verbatim; the caller MUST have verified
        ``chapter_id == twists.chapter_id`` first (service-layer
        invariant from data-model.md).
        """
        result = await self._s.execute(
            sa.text(
                "INSERT INTO votes (twist_id, user_id, chapter_id) "
                "VALUES (:twist_id, :user_id, :chapter_id) "
                "ON CONFLICT (twist_id, user_id) DO NOTHING "
                "RETURNING id"
            ),
            {
                "twist_id": twist_id,
                "user_id": user_id,
                "chapter_id": chapter_id,
            },
        )
        row = result.scalar_one_or_none()
        return int(row) if row is not None else None

    async def list_approved_with_vote_counts(
        self, chapter_id: int
    ) -> list[FeedRow]:
        """Return every approved twist for ``chapter_id`` with its current
        vote count.

        ``LEFT JOIN`` so twists with 0 votes still appear. Uses
        ``idx_twists_chapter_status`` for the WHERE filter and the
        UNIQUE index on ``votes(twist_id, user_id)`` for the aggregation.
        Ordering is deterministic by ``submitted_at ASC, id ASC`` so
        Python-side cursor pagination stays stable across calls.
        """
        result = await self._s.execute(
            sa.text(
                "SELECT t.id, t.public_id, t.content, "
                "       COUNT(v.id) AS vote_count, "
                "       t.submitted_at "
                "  FROM twists t "
                "  LEFT JOIN votes v ON v.twist_id = t.id "
                " WHERE t.chapter_id = :chapter_id "
                "   AND t.status = 'approved' "
                " GROUP BY t.id "
                " ORDER BY t.submitted_at ASC, t.id ASC"
            ),
            {"chapter_id": chapter_id},
        )
        return [
            FeedRow(
                id=int(row["id"]),
                public_id=UUID(str(row["public_id"])),
                content=str(row["content"]),
                vote_count=int(row["vote_count"]),
                submitted_at=row["submitted_at"],
            )
            for row in result.mappings()
        ]

    async def lock_user_chapter(
        self, user_id: int, chapter_id: int
    ) -> None:
        """Acquire ``pg_advisory_xact_lock`` for (user, chapter) with a
        1-second timeout.

        Transaction-scoped — released automatically on commit/rollback.
        Serializes concurrent vote-casts for the same (user, chapter) so
        the quota count + insert pair is race-safe (FR-006 / R-V1).

        Raises
        ------
        VoteLockBusy
            If the lock cannot be acquired within 1 second (55P03).
        """
        lock_key = f"vote_quota:{user_id}:{chapter_id}"
        try:
            await self._s.execute(
                sa.text("SET LOCAL lock_timeout = '1000ms'")
            )
            await self._s.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": lock_key},
            )
        except OperationalError as exc:
            orig = getattr(exc, "orig", None)
            if orig is not None and type(orig).__name__ == "LockNotAvailableError":
                raise VoteLockBusy(user_id, chapter_id) from exc
            raise
