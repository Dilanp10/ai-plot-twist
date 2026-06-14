"""Winner selection for the nightly generation pipeline.

Module 008 / Task T-001.

Implements the deterministic tiebreak rule from SDD §4.3:
  votes DESC, submitted_at ASC, id ASC

Only ``approved`` twists are considered; ``deleted_by_user`` and all
rejected statuses are excluded by the ``status = 'approved'`` filter.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

# Fetches the top-2 approved twists by vote count so the coordinator can
# detect a tie (row 2 exists and shares vote_count with row 1) and populate
# runner_up_twist_id in the manifest's winner_metadata (FR-002).
_WINNER_SQL = sa.text(
    "WITH ranked AS ("
    "  SELECT"
    "    t.id,"
    "    t.public_id,"
    "    u.display_name,"
    "    COUNT(v.id) AS vote_count,"
    "    t.submitted_at,"
    "    ROW_NUMBER() OVER ("
    "      ORDER BY COUNT(v.id) DESC, t.submitted_at ASC, t.id ASC"
    "    ) AS rn"
    "  FROM twists t"
    "  JOIN users u ON u.id = t.user_id"
    "  LEFT JOIN votes v ON v.twist_id = t.id"
    "  WHERE t.chapter_id = :chapter_id"
    "    AND t.status = 'approved'"
    "  GROUP BY t.id, t.public_id, t.submitted_at, u.display_name"
    ")"
    " SELECT id, public_id, display_name, vote_count, rn"
    " FROM ranked"
    " WHERE rn <= 2"
    " ORDER BY rn"
)


@dataclass(frozen=True)
class WinnerPick:
    """Result of the winner-selection query.

    All fields are ``None`` / 0 / False when no approved twists exist
    (auto-continue mode, SDD §4.3 "Caso degenerado").
    """

    winner_twist_id: int | None
    winner_public_id: UUID | None
    winner_user_display_name: str | None
    vote_count: int
    tiebreak: bool
    runner_up_twist_id: UUID | None


async def pick_winner(session: AsyncSession, chapter_id: int) -> WinnerPick:
    """Return the winning twist for *chapter_id*, or a null pick if none exist.

    The query reads from ``twists``, ``users``, and ``votes`` tables using a
    window function; the caller supplies an open ``AsyncSession`` and is
    responsible for transaction management.
    """
    result = await session.execute(_WINNER_SQL, {"chapter_id": chapter_id})
    rows = list(result.mappings())

    if not rows:
        return WinnerPick(
            winner_twist_id=None,
            winner_public_id=None,
            winner_user_display_name=None,
            vote_count=0,
            tiebreak=False,
            runner_up_twist_id=None,
        )

    winner = rows[0]
    runner_up = rows[1] if len(rows) > 1 else None

    tiebreak = (
        runner_up is not None
        and int(runner_up["vote_count"]) == int(winner["vote_count"])
    )
    runner_up_public_id: UUID | None = (
        UUID(str(runner_up["public_id"])) if runner_up is not None and tiebreak else None
    )

    return WinnerPick(
        winner_twist_id=int(winner["id"]),
        winner_public_id=UUID(str(winner["public_id"])),
        winner_user_display_name=str(winner["display_name"]),
        vote_count=int(winner["vote_count"]),
        tiebreak=tiebreak,
        runner_up_twist_id=runner_up_public_id,
    )
