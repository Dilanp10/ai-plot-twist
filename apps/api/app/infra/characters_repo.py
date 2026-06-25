"""CharactersRepo — read-only repository for the ``characters`` catalog.

Module 013 / Task T-003.

Backs the public ``GET /characters`` endpoint and the per-submission
``character_id`` validation in the module 005 delta. All methods are
read-only and operate on the caller-supplied ``AsyncSession``.

The catalog is small (≤12 active rows in MVP) so neither method paginates
nor uses caching beyond the partial index ``idx_characters_active_sort``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

_AspectRatio = Literal["1:1", "9:16", "16:9"]


@dataclass(frozen=True)
class CharacterRow:
    """Flat projection of a ``characters`` row, public-facing fields only.

    ``active``, ``sort_order``, ``created_at`` and ``updated_at`` are
    deliberately omitted — callers never need them. ``sort_order`` shapes
    the result order via the SQL ``ORDER BY``; the consumer just reads
    rows in the returned order.
    """

    id: int
    slug: str
    display_name: str
    photo_r2_key: str
    aspect_ratio: _AspectRatio


class CharactersRepo:
    """Read-only access to the ``characters`` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active(self) -> list[CharacterRow]:
        """Return active characters ordered by ``(sort_order ASC, id ASC)``.

        Inactive rows (``active = FALSE``) are filtered at the SQL level
        via the partial index ``idx_characters_active_sort``.
        """
        rows = (
            await self._session.execute(
                sa.text(
                    "SELECT id, slug, display_name, photo_r2_key, aspect_ratio "
                    "FROM characters "
                    "WHERE active = TRUE "
                    "ORDER BY sort_order ASC, id ASC"
                )
            )
        ).all()
        return [
            CharacterRow(
                id=r.id,
                slug=r.slug,
                display_name=r.display_name,
                photo_r2_key=r.photo_r2_key,
                aspect_ratio=r.aspect_ratio,
            )
            for r in rows
        ]

    async def get_by_id_if_active(self, character_id: int) -> CharacterRow | None:
        """Return the row for ``character_id`` iff it exists and is active.

        Used by the module 005 delta to validate ``twists.character_id`` at
        submission time. Inactive rows return ``None`` — a hidden character
        cannot be picked anew, but FKs from existing twists survive (the
        row is not deleted).
        """
        row = (
            await self._session.execute(
                sa.text(
                    "SELECT id, slug, display_name, photo_r2_key, aspect_ratio "
                    "FROM characters "
                    "WHERE id = :id AND active = TRUE"
                ),
                {"id": character_id},
            )
        ).first()
        if row is None:
            return None
        return CharacterRow(
            id=row.id,
            slug=row.slug,
            display_name=row.display_name,
            photo_r2_key=row.photo_r2_key,
            aspect_ratio=row.aspect_ratio,
        )
