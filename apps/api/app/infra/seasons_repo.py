"""SeasonsRepo — SQLAlchemy Core repository for the ``seasons`` table.

Module 003 / Task T-007.

All methods operate on the caller-supplied ``AsyncSession``; the caller
is responsible for committing or rolling back the transaction.

The ``uniq_one_active_season`` partial unique index (added in migration 0004)
enforces the single-active-season invariant at the DB level.  Callers that
want to replace the active season should call ``mark_inactive`` first within
the same transaction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class Season:
    """Flat projection of a ``seasons`` row (no JSONB blobs)."""

    id: int
    slug: str
    title: str
    is_active: bool
    started_on: date


def _map_row(row: Any) -> Season:
    return Season(
        id=int(row["id"]),
        slug=str(row["slug"]),
        title=str(row["title"]),
        is_active=bool(row["is_active"]),
        started_on=row["started_on"],
    )


_SELECT_COLS = "id, slug, title, is_active, started_on"


class SeasonsRepo:
    """Repository for the ``seasons`` table.

    Parameters
    ----------
    session:
        Active ``AsyncSession``.  Caller manages commit/rollback.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def insert(
        self,
        slug: str,
        title: str,
        bible_json: dict[str, Any],
        started_on: date,
    ) -> int:
        """Insert a new season row and return its ``id``.

        The new season is active by default (``is_active = TRUE``).
        A ``UniqueViolation`` is raised by the DB if another active season
        already exists (``uniq_one_active_season``).

        Parameters
        ----------
        slug:
            Machine-readable identifier (e.g. ``"s01"``).
        title:
            Human-readable title.
        bible_json:
            Season bible as a JSON-serialisable dict.
        started_on:
            Calendar date the season begins.

        Returns
        -------
        int
            The newly assigned ``seasons.id``.
        """
        result = await self._s.execute(
            sa.text(
                "INSERT INTO seasons (slug, title, bible_json, started_on) "
                "VALUES (:slug, :title, :bible_json::jsonb, :started_on) "
                f"RETURNING {_SELECT_COLS}"
            ),
            {
                "slug": slug,
                "title": title,
                "bible_json": json.dumps(bible_json),
                "started_on": started_on,
            },
        )
        row = result.mappings().one()
        return int(row["id"])

    async def get_active(self) -> Season | None:
        """Return the currently active season, or *None* if none exists."""
        result = await self._s.execute(
            sa.text(
                f"SELECT {_SELECT_COLS} FROM seasons "
                "WHERE is_active = TRUE LIMIT 1"
            )
        )
        row = result.mappings().one_or_none()
        return _map_row(row) if row is not None else None

    async def get_by_slug(self, slug: str) -> Season | None:
        """Return a season by its slug, or *None* if not found."""
        result = await self._s.execute(
            sa.text(
                f"SELECT {_SELECT_COLS} FROM seasons WHERE slug = :slug"
            ),
            {"slug": slug},
        )
        row = result.mappings().one_or_none()
        return _map_row(row) if row is not None else None

    async def mark_inactive(self, season_id: int) -> None:
        """Set ``is_active = FALSE`` for the given season.

        Does not commit; caller is responsible for the transaction.
        """
        await self._s.execute(
            sa.text(
                "UPDATE seasons SET is_active = FALSE WHERE id = :id"
            ),
            {"id": season_id},
        )
