"""CyclesRepo — SQLAlchemy Core repository for the ``cycles`` table.

Module 003 / Task T-009.

All methods operate on the caller-supplied ``AsyncSession``; the caller
is responsible for committing or rolling back the transaction.

Advisory lock:
  ``lock_advisory`` acquires ``pg_advisory_xact_lock`` keyed on
  ``'cycle:<id>'`` with a 2-second timeout.  The lock is automatically
  released when the surrounding transaction commits or rolls back.

  Raises ``LockBusy`` if the lock cannot be acquired within 2 s.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LockBusy(Exception):
    """Raised when the advisory lock for a cycle cannot be acquired within 2 s.

    The executor should translate this to HTTP 503 with
    ``{"code": "lock_busy"}``.
    """

    def __init__(self, cycle_id: int) -> None:
        self.cycle_id = cycle_id
        super().__init__(
            f"Advisory lock for cycle {cycle_id} could not be acquired within 2 s"
        )


# ---------------------------------------------------------------------------
# Row projection
# ---------------------------------------------------------------------------


@dataclass
class CycleRow:
    """Flat projection of a ``cycles`` row."""

    id: int
    season_id: int
    chapter_id: int
    next_chapter_id: int | None
    state: str
    state_entered_at: datetime
    cycle_date: date


def _map_row(row: Any) -> CycleRow:
    next_ch: int | None = row["next_chapter_id"]
    return CycleRow(
        id=int(row["id"]),
        season_id=int(row["season_id"]),
        chapter_id=int(row["chapter_id"]),
        next_chapter_id=int(next_ch) if next_ch is not None else None,
        state=str(row["state"]),
        state_entered_at=row["state_entered_at"],
        cycle_date=row["cycle_date"],
    )


_SELECT_COLS = (
    "id, season_id, chapter_id, next_chapter_id, "
    "state, state_entered_at, cycle_date"
)


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class CyclesRepo:
    """Repository for the ``cycles`` table.

    Parameters
    ----------
    session:
        Active ``AsyncSession``.  Caller manages commit/rollback.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def insert(
        self,
        season_id: int,
        chapter_id: int,
        cycle_date: date,
    ) -> int:
        """Create a new cycle in ``PENDING_RELEASE`` state.

        Parameters
        ----------
        season_id:
            FK to ``seasons.id``.
        chapter_id:
            FK to ``chapters.id`` (the chapter being released today).
        cycle_date:
            Calendar date for this cycle (ART local date).

        Returns
        -------
        int
            The newly assigned ``cycles.id``.
        """
        result = await self._s.execute(
            sa.text(
                "INSERT INTO cycles (season_id, chapter_id, state, cycle_date) "
                "VALUES (:season_id, :chapter_id, 'PENDING_RELEASE', :cycle_date) "
                f"RETURNING {_SELECT_COLS}"
            ),
            {
                "season_id": season_id,
                "chapter_id": chapter_id,
                "cycle_date": cycle_date,
            },
        )
        row = result.mappings().one()
        return int(row["id"])

    async def get_active(self) -> CycleRow | None:
        """Return the most recent cycle for the currently active season.

        Returns *None* if no active season or no cycle exists yet.
        """
        result = await self._s.execute(
            sa.text(
                f"SELECT c.{', c.'.join(_SELECT_COLS.split(', '))} "
                "FROM cycles c "
                "JOIN seasons s ON s.id = c.season_id "
                "WHERE s.is_active = TRUE "
                "ORDER BY c.cycle_date DESC "
                "LIMIT 1"
            )
        )
        row = result.mappings().one_or_none()
        return _map_row(row) if row is not None else None

    async def get_by_chapter_id(self, chapter_id: int) -> CycleRow | None:
        """Return the most recent cycle that references ``chapter_id``.

        Module 006 / T-010: the director-filter side-effect closure needs
        the ``cycle_id`` to build the ``state_transitions.trigger_id`` for
        the VOTACION transition (FR-012). The closure only receives the
        ``chapter_id`` (matches the registry signature
        ``(int) -> Awaitable[None]``).

        Ordering by ``cycle_date DESC`` mirrors :meth:`get_active`, so the
        same chapter being referenced by an old FAILED cycle and a fresh
        active one returns the fresh one.
        """
        result = await self._s.execute(
            sa.text(
                f"SELECT {_SELECT_COLS} FROM cycles "
                "WHERE chapter_id = :chapter_id "
                "ORDER BY cycle_date DESC "
                "LIMIT 1"
            ),
            {"chapter_id": chapter_id},
        )
        row = result.mappings().one_or_none()
        return _map_row(row) if row is not None else None

    async def update_state(
        self,
        cycle_id: int,
        new_state: str,
        *,
        next_chapter_id: int | None = None,
    ) -> None:
        """Transition the cycle to *new_state*.

        Always sets ``state_entered_at = now()``.  If *next_chapter_id* is
        provided it overwrites the column; otherwise the existing value is
        preserved via ``COALESCE``.

        Parameters
        ----------
        cycle_id:
            PK of the cycle to update.
        new_state:
            Target FSM state (must be a valid ``ck_cycles_state`` value).
        next_chapter_id:
            When the generation pipeline completes it sets this to the newly
            created chapter's id.  Pass *None* to leave the existing value
            unchanged.
        """
        await self._s.execute(
            sa.text(
                "UPDATE cycles "
                "SET state = :state, "
                "    state_entered_at = now(), "
                "    next_chapter_id = COALESCE(:next_chapter_id, next_chapter_id) "
                "WHERE id = :id"
            ),
            {
                "id": cycle_id,
                "state": new_state,
                "next_chapter_id": next_chapter_id,
            },
        )

    async def advance_chapter(self, cycle_id: int) -> None:
        """Promote ``next_chapter_id`` to ``chapter_id`` and clear it.

        Called on the ESTRENO release: the chapter generated during the
        previous cycle (held in ``next_chapter_id``) becomes the live
        chapter the cycle points at. No-op when ``next_chapter_id`` is
        NULL (e.g. the very first bootstrap release).
        """
        await self._s.execute(
            sa.text(
                "UPDATE cycles "
                "SET chapter_id = next_chapter_id, "
                "    next_chapter_id = NULL "
                "WHERE id = :id AND next_chapter_id IS NOT NULL"
            ),
            {"id": cycle_id},
        )

    async def lock_advisory(self, cycle_id: int) -> None:
        """Acquire ``pg_advisory_xact_lock`` for this cycle with a 2 s timeout.

        The lock is transaction-scoped — it is released automatically when
        the caller's transaction commits or rolls back.

        Raises
        ------
        LockBusy
            If the lock cannot be acquired within 2 seconds (55P03).
        """
        lock_key = f"cycle:{cycle_id}"
        try:
            # SET LOCAL only affects the current transaction.
            await self._s.execute(sa.text("SET LOCAL lock_timeout = '2000ms'"))
            await self._s.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": lock_key},
            )
        except OperationalError as exc:
            orig = getattr(exc, "orig", None)
            if orig is not None and type(orig).__name__ == "LockNotAvailableError":
                raise LockBusy(cycle_id) from exc
            raise
