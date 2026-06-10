"""ContentRepo — joined read paths for the chapter content endpoints.

Module 004 / Task T-004.

Three single-round-trip queries (Q-1, Q-2, Q-3 in data-model.md) that source
``/chapters/today``, ``/chapters/{public_id}`` and ``/seasons/{slug}``.

The repo is **read-only**: no INSERT/UPDATE/DELETE, no advisory locks, no
commits. Callers pass an ``AsyncSession``; the same session is reused for the
short transaction (which auto-rolls-back when the request handler returns).

SQL is raw text (``sa.text``) — the queries are stable and we want explicit
control over the join shape (see research R-003).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Payload types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TodayPayload:
    """Flat projection of Q-1 (active cycle ⨝ chapter ⨝ season).

    ``chapter_released_at`` is nullable: the bootstrap chapter is ``status=
    ready`` until the first ESTRENO tick fires, with ``released_at = NULL``.
    """

    cycle_id: int
    cycle_state: str
    cycle_state_entered_at: datetime
    cycle_date: date

    chapter_public_id: UUID
    chapter_day_index: int
    chapter_title: str
    chapter_synopsis: str
    chapter_manifest_json: dict[str, Any]
    chapter_released_at: datetime | None
    chapter_status: str

    season_slug: str
    season_title: str


@dataclass(frozen=True)
class ChapterPayload:
    """Projection of Q-2 — a public-addressable chapter (live or archived).

    ``released_at`` is non-null: the Q-2 ``status IN ('live','archived')``
    filter excludes pre-release rows where ``released_at`` could still be NULL.
    """

    public_id: UUID
    day_index: int
    title: str
    synopsis: str
    manifest_json: dict[str, Any]
    released_at: datetime
    status: str
    season_slug: str
    season_title: str


@dataclass(frozen=True)
class SeasonPayload:
    """Projection of Q-3 — season meta + aggregated counts.

    ``current_day_index`` is nullable: ``MAX(day_index)`` is NULL when the
    season has no ``status='live'`` chapter yet (e.g. day-0 just bootstrapped).
    """

    slug: str
    title: str
    bible_json: dict[str, Any]
    started_on: date
    ended_on: date | None
    chapter_count: int
    current_day_index: int | None


# ---------------------------------------------------------------------------
# SQL — copy of data-model.md §Required read queries (single source of truth)
# ---------------------------------------------------------------------------

_Q1_ACTIVE_TODAY = """
SELECT
  c.id               AS cycle_id,
  c.state            AS cycle_state,
  c.state_entered_at,
  c.cycle_date,
  ch.public_id       AS chapter_public_id,
  ch.day_index       AS chapter_day_index,
  ch.title           AS chapter_title,
  ch.synopsis        AS chapter_synopsis,
  ch.manifest_json   AS chapter_manifest_json,
  ch.released_at     AS chapter_released_at,
  ch.status          AS chapter_status,
  s.slug             AS season_slug,
  s.title            AS season_title
FROM cycles c
JOIN chapters ch ON ch.id = c.chapter_id
JOIN seasons  s  ON s.id  = c.season_id
WHERE s.is_active = TRUE
LIMIT 1
""".strip()


_Q2_CHAPTER_BY_PUBLIC_ID = """
SELECT
  ch.public_id,
  ch.day_index,
  ch.title,
  ch.synopsis,
  ch.manifest_json,
  ch.released_at,
  ch.status,
  s.slug  AS season_slug,
  s.title AS season_title
FROM chapters ch
JOIN seasons s ON s.id = ch.season_id
WHERE ch.public_id = :public_id
  AND ch.status IN ('live', 'archived')
LIMIT 1
""".strip()


_Q3_SEASON_BY_SLUG = """
SELECT
  s.slug,
  s.title,
  s.bible_json,
  s.started_on,
  s.ended_on,
  COUNT(ch.id) FILTER (
    WHERE ch.status IN ('live', 'archived')
  ) AS chapter_count,
  (SELECT MAX(ch2.day_index)
     FROM chapters ch2
     WHERE ch2.season_id = s.id
       AND ch2.status = 'live') AS current_day_index
FROM seasons s
LEFT JOIN chapters ch ON ch.season_id = s.id
WHERE s.slug = :slug
GROUP BY s.id
""".strip()


# ---------------------------------------------------------------------------
# Row → dataclass mappers
# ---------------------------------------------------------------------------


def _map_today(row: Any) -> TodayPayload:
    return TodayPayload(
        cycle_id=int(row["cycle_id"]),
        cycle_state=str(row["cycle_state"]),
        cycle_state_entered_at=row["state_entered_at"],
        cycle_date=row["cycle_date"],
        chapter_public_id=row["chapter_public_id"],
        chapter_day_index=int(row["chapter_day_index"]),
        chapter_title=str(row["chapter_title"]),
        chapter_synopsis=str(row["chapter_synopsis"]),
        chapter_manifest_json=dict(row["chapter_manifest_json"]),
        chapter_released_at=row["chapter_released_at"],
        chapter_status=str(row["chapter_status"]),
        season_slug=str(row["season_slug"]),
        season_title=str(row["season_title"]),
    )


def _map_chapter(row: Any) -> ChapterPayload:
    return ChapterPayload(
        public_id=row["public_id"],
        day_index=int(row["day_index"]),
        title=str(row["title"]),
        synopsis=str(row["synopsis"]),
        manifest_json=dict(row["manifest_json"]),
        released_at=row["released_at"],
        status=str(row["status"]),
        season_slug=str(row["season_slug"]),
        season_title=str(row["season_title"]),
    )


def _map_season(row: Any) -> SeasonPayload:
    raw_current = row["current_day_index"]
    return SeasonPayload(
        slug=str(row["slug"]),
        title=str(row["title"]),
        bible_json=dict(row["bible_json"]),
        started_on=row["started_on"],
        ended_on=row["ended_on"],
        chapter_count=int(row["chapter_count"]),
        current_day_index=int(raw_current) if raw_current is not None else None,
    )


# ---------------------------------------------------------------------------
# Repo
# ---------------------------------------------------------------------------


class ContentRepo:
    """Read-only joined queries that feed the chapter content endpoints."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_today_payload(self) -> TodayPayload | None:
        """Return the active cycle + its chapter + its season, or *None*.

        ``None`` means there is no row with ``seasons.is_active = TRUE`` — the
        service maps this to a 503 ``no_active_season`` response.
        """
        result = await self._s.execute(sa.text(_Q1_ACTIVE_TODAY))
        row = result.mappings().one_or_none()
        return _map_today(row) if row is not None else None

    async def get_chapter_by_public_id(self, public_id: UUID) -> ChapterPayload | None:
        """Return a chapter with ``status IN ('live','archived')`` by public id.

        Pre-release statuses (``draft``, ``generating``, ``ready``,
        ``ready_degraded``) and unknown ids both return *None* — both surface
        to the client as 404 ``chapter_not_found``.
        """
        result = await self._s.execute(
            sa.text(_Q2_CHAPTER_BY_PUBLIC_ID),
            {"public_id": public_id},
        )
        row = result.mappings().one_or_none()
        return _map_chapter(row) if row is not None else None

    async def get_season_by_slug(self, slug: str) -> SeasonPayload | None:
        """Return season meta + chapter_count + current_day_index, or *None*.

        ``current_day_index`` is ``None`` when the season has no ``live``
        chapter yet (subquery ``MAX(day_index)`` returns NULL).
        """
        result = await self._s.execute(sa.text(_Q3_SEASON_BY_SLUG), {"slug": slug})
        row = result.mappings().one_or_none()
        return _map_season(row) if row is not None else None
