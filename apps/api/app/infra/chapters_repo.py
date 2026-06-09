"""ChaptersRepo — SQLAlchemy Core repository for the ``chapters`` table.

Module 003 / Task T-008.

All methods operate on the caller-supplied ``AsyncSession``; the caller
is responsible for committing or rolling back the transaction.

Column notes:
  - ``public_id`` is a DB-generated UUID (gen_random_uuid()); callers never
    supply it.
  - ``manifest_json`` holds the chapter panel manifest.
  - ``status`` is constrained by ``ck_chapters_status``:
    draft | generating | ready | ready_degraded | live | archived
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class Chapter:
    """Flat projection of a ``chapters`` row (no manifest_json blob)."""

    id: int
    public_id: UUID
    season_id: int
    day_index: int
    title: str
    status: str
    released_at: datetime | None
    created_at: datetime


def _map_row(row: Any) -> Chapter:
    return Chapter(
        id=int(row["id"]),
        public_id=UUID(str(row["public_id"])),
        season_id=int(row["season_id"]),
        day_index=int(row["day_index"]),
        title=str(row["title"]),
        status=str(row["status"]),
        released_at=row["released_at"],
        created_at=row["created_at"],
    )


_SELECT_COLS = (
    "id, public_id, season_id, day_index, title, "
    "status, released_at, created_at"
)


class ChaptersRepo:
    """Repository for the ``chapters`` table.

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
        day_index: int,
        title: str,
        synopsis: str,
        manifest_json: dict[str, Any],
        status: str = "ready",
    ) -> int:
        """Insert a new chapter and return its ``id``.

        Parameters
        ----------
        season_id:
            FK to ``seasons.id``.
        day_index:
            Day number within the season (1-based).
        title:
            Chapter title.
        synopsis:
            Chapter synopsis.
        manifest_json:
            Panel manifest as a JSON-serialisable dict.
        status:
            One of: draft | generating | ready | ready_degraded | live | archived.
            Defaults to ``'ready'``.

        Returns
        -------
        int
            The newly assigned ``chapters.id``.
        """
        result = await self._s.execute(
            sa.text(
                "INSERT INTO chapters "
                "(season_id, day_index, title, synopsis, manifest_json, status) "
                "VALUES "
                "(:season_id, :day_index, :title, :synopsis, "
                ":manifest_json::jsonb, :status) "
                f"RETURNING {_SELECT_COLS}"
            ),
            {
                "season_id": season_id,
                "day_index": day_index,
                "title": title,
                "synopsis": synopsis,
                "manifest_json": json.dumps(manifest_json),
                "status": status,
            },
        )
        row = result.mappings().one()
        return int(row["id"])

    async def get_by_id(self, chapter_id: int) -> Chapter | None:
        """Return a chapter by its internal id, or *None* if not found."""
        result = await self._s.execute(
            sa.text(
                f"SELECT {_SELECT_COLS} FROM chapters WHERE id = :id"
            ),
            {"id": chapter_id},
        )
        row = result.mappings().one_or_none()
        return _map_row(row) if row is not None else None

    async def clone_manifest(
        self,
        src_id: int,
        next_day_index: int,
    ) -> int:
        """Create a new chapter by cloning *src_id*'s manifest and metadata.

        The cloned chapter starts with ``status='draft'`` and no
        ``released_at``.  The original chapter is unchanged.

        Parameters
        ----------
        src_id:
            ``chapters.id`` of the source chapter.
        next_day_index:
            ``day_index`` for the new chapter.

        Returns
        -------
        int
            The newly assigned ``chapters.id``.

        Raises
        ------
        sqlalchemy.exc.NoResultFound
            If *src_id* does not exist.
        """
        result = await self._s.execute(
            sa.text(
                "INSERT INTO chapters "
                "(season_id, day_index, title, synopsis, manifest_json, status) "
                "SELECT season_id, :next_day_index, title, synopsis, "
                "       manifest_json, 'draft' "
                "FROM chapters WHERE id = :src_id "
                f"RETURNING {_SELECT_COLS}"
            ),
            {"src_id": src_id, "next_day_index": next_day_index},
        )
        row = result.mappings().one()
        return int(row["id"])

    async def mark_live(self, chapter_id: int) -> None:
        """Set ``status = 'live'`` and ``released_at = now()`` on a chapter.

        Does not commit; caller is responsible for the transaction.
        """
        await self._s.execute(
            sa.text(
                "UPDATE chapters "
                "SET status = 'live', released_at = now() "
                "WHERE id = :id"
            ),
            {"id": chapter_id},
        )

    async def list_by_season(self, season_id: int) -> list[Chapter]:
        """Return all chapters for a season ordered by ``day_index`` ASC."""
        result = await self._s.execute(
            sa.text(
                f"SELECT {_SELECT_COLS} FROM chapters "
                "WHERE season_id = :season_id "
                "ORDER BY day_index ASC"
            ),
            {"season_id": season_id},
        )
        return [_map_row(row) for row in result.mappings()]
