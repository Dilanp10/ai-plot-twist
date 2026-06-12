"""TransitionsRepo — SQLAlchemy Core repository for ``state_transitions``.

Module 003 / Task T-010.

All methods operate on the caller-supplied ``AsyncSession``; the caller
is responsible for committing or rolling back the transaction.

Idempotency:
  ``insert`` uses ``ON CONFLICT (cycle_id, to_state, trigger_id) WHERE
  trigger_id IS NOT NULL DO NOTHING``.  When the partial UNIQUE index fires
  (duplicate cron replay), no row is inserted and *None* is returned.
  The executor translates *None* to a 200 ``already_applied`` response.

  Rows with ``trigger_id IS NULL`` always insert (no conflict detection).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Row projection
# ---------------------------------------------------------------------------


@dataclass
class TransitionRow:
    """Flat projection of a ``state_transitions`` row."""

    id: int
    cycle_id: int
    from_state: str
    to_state: str
    triggered_by: str
    trigger_id: str | None
    payload_json: dict[str, Any] | None
    created_at: datetime


def _map_row(row: Any) -> TransitionRow:
    raw_payload = row["payload_json"]
    return TransitionRow(
        id=int(row["id"]),
        cycle_id=int(row["cycle_id"]),
        from_state=str(row["from_state"]),
        to_state=str(row["to_state"]),
        triggered_by=str(row["triggered_by"]),
        trigger_id=str(row["trigger_id"]) if row["trigger_id"] is not None else None,
        payload_json=dict(raw_payload) if raw_payload is not None else None,
        created_at=row["created_at"],
    )


_SELECT_COLS = (
    "id, cycle_id, from_state, to_state, triggered_by, "
    "trigger_id, payload_json, created_at"
)


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class TransitionsRepo:
    """Repository for the ``state_transitions`` table.

    Parameters
    ----------
    session:
        Active ``AsyncSession``.  Caller manages commit/rollback.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def insert(
        self,
        cycle_id: int,
        from_state: str,
        to_state: str,
        triggered_by: str,
        trigger_id: str | None = None,
        payload_json: dict[str, Any] | None = None,
    ) -> TransitionRow | None:
        """Insert a transition row with idempotency protection.

        If a row already exists with the same ``(cycle_id, to_state,
        trigger_id)`` *and* ``trigger_id IS NOT NULL``, the insert is
        skipped and *None* is returned (idempotency — duplicate cron replay).

        When ``trigger_id`` is *None* no conflict detection applies and a
        new row is always inserted.

        Parameters
        ----------
        cycle_id:
            FK to ``cycles.id``.
        from_state:
            FSM state the cycle was in before this transition.
        to_state:
            FSM state the cycle is moving to.
        triggered_by:
            Vocabulary: cron | admin | retry | side_effect | watchdog.
        trigger_id:
            Opaque idempotency key (e.g. GitHub run id).  *None* for
            internally-triggered transitions (side effects, watchdog).
        payload_json:
            Optional structured context (error hash, run metadata, …).

        Returns
        -------
        TransitionRow | None
            The inserted row, or *None* if a duplicate ``trigger_id`` was
            detected (already applied).
        """
        result = await self._s.execute(
            sa.text(
                f"INSERT INTO state_transitions "
                f"(cycle_id, from_state, to_state, triggered_by, "
                f" trigger_id, payload_json) "
                f"VALUES (:cycle_id, :from_state, :to_state, :triggered_by, "
                f"        :trigger_id, cast(:payload_json AS jsonb)) "
                f"ON CONFLICT (cycle_id, to_state, trigger_id) "
                f"WHERE trigger_id IS NOT NULL DO NOTHING "
                f"RETURNING {_SELECT_COLS}"
            ),
            {
                "cycle_id": cycle_id,
                "from_state": from_state,
                "to_state": to_state,
                "triggered_by": triggered_by,
                "trigger_id": trigger_id,
                "payload_json": (
                    json.dumps(payload_json) if payload_json is not None else None
                ),
            },
        )
        row = result.mappings().one_or_none()
        return _map_row(row) if row is not None else None

    async def get_recent(
        self,
        cycle_id: int,
        limit: int = 5,
    ) -> list[TransitionRow]:
        """Return the most recent *limit* transitions for a cycle.

        Ordered by ``created_at DESC`` (newest first).
        """
        result = await self._s.execute(
            sa.text(
                f"SELECT {_SELECT_COLS} FROM state_transitions "
                "WHERE cycle_id = :cycle_id "
                "ORDER BY created_at DESC "
                "LIMIT :limit"
            ),
            {"cycle_id": cycle_id, "limit": limit},
        )
        return [_map_row(row) for row in result.mappings()]

    async def get_by_trigger(
        self,
        cycle_id: int,
        to_state: str,
        trigger_id: str,
    ) -> TransitionRow | None:
        """Find a specific transition by its idempotency key.

        Used to retrieve the original ``applied_at`` timestamp for the
        ``already_applied`` 200 response.

        Parameters
        ----------
        cycle_id:
            FK to ``cycles.id``.
        to_state:
            Target state of the transition to look up.
        trigger_id:
            The opaque idempotency key (must not be *None*).

        Returns
        -------
        TransitionRow | None
            The matching row, or *None* if not found.
        """
        result = await self._s.execute(
            sa.text(
                f"SELECT {_SELECT_COLS} FROM state_transitions "
                "WHERE cycle_id = :cycle_id "
                "  AND to_state = :to_state "
                "  AND trigger_id = :trigger_id"
            ),
            {
                "cycle_id": cycle_id,
                "to_state": to_state,
                "trigger_id": trigger_id,
            },
        )
        row = result.mappings().one_or_none()
        return _map_row(row) if row is not None else None
