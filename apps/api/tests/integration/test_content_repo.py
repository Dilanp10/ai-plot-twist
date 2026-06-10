"""Integration tests: ContentRepo (joined read paths for module 004).

Module 004 / Task T-004.

Skips when DATABASE_URL is the conftest placeholder (no real DB available).
Each test seeds its own data via direct SQL and cleans up at the end.
Uses a unique slug/uuid prefix to avoid collisions across runs.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.infra.content_repo import (
    ChapterPayload,
    ContentRepo,
    SeasonPayload,
    TodayPayload,
)

API_DIR = Path(__file__).parent.parent.parent
ALEMBIC_INI = API_DIR / "alembic.ini"

_SLUG_PREFIX = "_cr-test-"
_TODAY = date(2026, 6, 9)


def _alembic_config(database_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _slug(suffix: str) -> str:
    return f"{_SLUG_PREFIX}{suffix}"


@pytest.fixture(scope="module")
def database_url() -> str:
    from tests.conftest import _is_placeholder_database_url

    url = os.environ.get("DATABASE_URL", "")
    if not url or _is_placeholder_database_url(url):
        pytest.skip("DATABASE_URL no apunta a una base real.")
    return url


@pytest.fixture(scope="module", autouse=True)
def _ensure_migrated(database_url: str) -> None:
    cfg = _alembic_config(database_url)
    asyncio.get_event_loop().run_until_complete(asyncio.to_thread(command.upgrade, cfg, "head"))


@pytest.fixture
async def session(database_url: str) -> AsyncSession:  # type: ignore[misc]
    engine = create_async_engine(database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


# ---------------------------------------------------------------------------
# Seed helpers — insert a season, chapter and cycle in one transaction
# ---------------------------------------------------------------------------


async def _deactivate_all_seasons(s: AsyncSession) -> None:
    await s.execute(sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE"))
    await s.commit()


async def _insert_season(
    s: AsyncSession,
    *,
    slug: str,
    title: str = "Season Test",
    bible: dict[str, object] | None = None,
    started_on: date = _TODAY,
    ended_on: date | None = None,
    is_active: bool = True,
) -> int:
    bible = bible if bible is not None else {"theme": "X"}
    result = await s.execute(
        sa.text(
            "INSERT INTO seasons (slug, title, bible_json, started_on, ended_on, "
            "is_active) "
            "VALUES (:slug, :title, :bible::jsonb, :started_on, :ended_on, "
            ":is_active) RETURNING id"
        ),
        {
            "slug": slug,
            "title": title,
            "bible": json.dumps(bible),
            "started_on": started_on,
            "ended_on": ended_on,
            "is_active": is_active,
        },
    )
    return int(result.scalar_one())


async def _insert_chapter(
    s: AsyncSession,
    *,
    season_id: int,
    day_index: int,
    status: str,
    title: str = "Chapter Test",
    synopsis: str = "An interesting day.",
    manifest: dict[str, object] | None = None,
    released_at: datetime | None = None,
    public_id: UUID | None = None,
) -> tuple[int, UUID]:
    manifest = manifest if manifest is not None else {"panels": [], "cliffhanger": "..."}
    public_id = public_id if public_id is not None else uuid4()
    result = await s.execute(
        sa.text(
            "INSERT INTO chapters (public_id, season_id, day_index, title, synopsis, "
            "manifest_json, status, released_at) "
            "VALUES (:public_id, :season_id, :day_index, :title, :synopsis, "
            ":manifest::jsonb, :status, :released_at) RETURNING id"
        ),
        {
            "public_id": public_id,
            "season_id": season_id,
            "day_index": day_index,
            "title": title,
            "synopsis": synopsis,
            "manifest": json.dumps(manifest),
            "status": status,
            "released_at": released_at,
        },
    )
    return int(result.scalar_one()), public_id


async def _insert_cycle(
    s: AsyncSession,
    *,
    season_id: int,
    chapter_id: int,
    state: str = "RECEPCION_IDEAS",
    state_entered_at: datetime | None = None,
    cycle_date: date = _TODAY,
) -> int:
    state_entered_at = state_entered_at or datetime(2026, 6, 9, 15, 0, tzinfo=UTC)
    result = await s.execute(
        sa.text(
            "INSERT INTO cycles (season_id, chapter_id, state, state_entered_at, "
            "cycle_date) "
            "VALUES (:season_id, :chapter_id, :state, :state_entered_at, :cycle_date) "
            "RETURNING id"
        ),
        {
            "season_id": season_id,
            "chapter_id": chapter_id,
            "state": state,
            "state_entered_at": state_entered_at,
            "cycle_date": cycle_date,
        },
    )
    return int(result.scalar_one())


async def _cleanup(s: AsyncSession) -> None:
    """Cascade-delete via seasons FK clears chapters + cycles too."""
    await s.execute(sa.text(f"DELETE FROM seasons WHERE slug LIKE '{_SLUG_PREFIX}%'"))
    await s.commit()


# ---------------------------------------------------------------------------
# Q-1 — get_today_payload
# ---------------------------------------------------------------------------


async def test_get_today_payload_returns_active_cycle(session: AsyncSession) -> None:
    await _deactivate_all_seasons(session)
    try:
        season_id = await _insert_season(
            session,
            slug=_slug("today-001"),
            title="Today Test",
        )
        chapter_id, public_id = await _insert_chapter(
            session,
            season_id=season_id,
            day_index=1,
            status="live",
            title="Day 1: The Signal",
            released_at=datetime(2026, 6, 9, 15, 0, tzinfo=UTC),
        )
        await _insert_cycle(
            session,
            season_id=season_id,
            chapter_id=chapter_id,
            state="RECEPCION_IDEAS",
        )
        await session.commit()

        payload = await ContentRepo(session).get_today_payload()
        assert payload is not None
        assert isinstance(payload, TodayPayload)
        assert payload.cycle_state == "RECEPCION_IDEAS"
        assert payload.chapter_public_id == public_id
        assert payload.chapter_day_index == 1
        assert payload.chapter_title == "Day 1: The Signal"
        assert payload.chapter_status == "live"
        assert payload.chapter_released_at == datetime(2026, 6, 9, 15, 0, tzinfo=UTC)
        assert payload.season_slug == _slug("today-001")
        assert payload.season_title == "Today Test"
    finally:
        await _cleanup(session)


async def test_get_today_payload_with_ready_chapter_has_null_released_at(
    session: AsyncSession,
) -> None:
    """Bootstrap state: cycle exists in PENDING_RELEASE pointing to a ``ready``
    chapter whose ``released_at`` is NULL until the first ESTRENO tick."""
    await _deactivate_all_seasons(session)
    try:
        season_id = await _insert_season(session, slug=_slug("today-002"))
        chapter_id, _ = await _insert_chapter(
            session,
            season_id=season_id,
            day_index=1,
            status="ready",
            released_at=None,
        )
        await _insert_cycle(
            session,
            season_id=season_id,
            chapter_id=chapter_id,
            state="PENDING_RELEASE",
        )
        await session.commit()

        payload = await ContentRepo(session).get_today_payload()
        assert payload is not None
        assert payload.cycle_state == "PENDING_RELEASE"
        assert payload.chapter_status == "ready"
        assert payload.chapter_released_at is None
    finally:
        await _cleanup(session)


async def test_get_today_payload_returns_none_when_no_active_season(
    session: AsyncSession,
) -> None:
    await _deactivate_all_seasons(session)
    try:
        # Insert an inactive season — should be invisible to Q-1.
        season_id = await _insert_season(session, slug=_slug("today-003"), is_active=False)
        chapter_id, _ = await _insert_chapter(
            session,
            season_id=season_id,
            day_index=1,
            status="live",
            released_at=datetime(2026, 6, 9, 15, 0, tzinfo=UTC),
        )
        await _insert_cycle(session, season_id=season_id, chapter_id=chapter_id)
        await session.commit()

        payload = await ContentRepo(session).get_today_payload()
        assert payload is None
    finally:
        await _cleanup(session)


# ---------------------------------------------------------------------------
# Q-2 — get_chapter_by_public_id
# ---------------------------------------------------------------------------


async def test_get_chapter_by_public_id_returns_live(session: AsyncSession) -> None:
    await _deactivate_all_seasons(session)
    try:
        season_id = await _insert_season(session, slug=_slug("chap-live"))
        _, public_id = await _insert_chapter(
            session,
            season_id=season_id,
            day_index=1,
            status="live",
            released_at=datetime(2026, 6, 9, 15, 0, tzinfo=UTC),
        )
        await session.commit()

        chapter = await ContentRepo(session).get_chapter_by_public_id(public_id)
        assert chapter is not None
        assert isinstance(chapter, ChapterPayload)
        assert chapter.public_id == public_id
        assert chapter.status == "live"
        assert chapter.season_slug == _slug("chap-live")
    finally:
        await _cleanup(session)


async def test_get_chapter_by_public_id_returns_archived(
    session: AsyncSession,
) -> None:
    await _deactivate_all_seasons(session)
    try:
        season_id = await _insert_season(session, slug=_slug("chap-arch"))
        _, public_id = await _insert_chapter(
            session,
            season_id=season_id,
            day_index=2,
            status="archived",
            released_at=datetime(2026, 6, 9, 15, 0, tzinfo=UTC),
        )
        await session.commit()

        chapter = await ContentRepo(session).get_chapter_by_public_id(public_id)
        assert chapter is not None
        assert chapter.status == "archived"
    finally:
        await _cleanup(session)


@pytest.mark.parametrize("pre_release_status", ["draft", "generating", "ready", "ready_degraded"])
async def test_get_chapter_by_public_id_returns_none_for_pre_release(
    session: AsyncSession, pre_release_status: str
) -> None:
    await _deactivate_all_seasons(session)
    try:
        season_id = await _insert_season(session, slug=_slug(f"chap-{pre_release_status}"))
        _, public_id = await _insert_chapter(
            session,
            season_id=season_id,
            day_index=1,
            status=pre_release_status,
            released_at=None,
        )
        await session.commit()

        chapter = await ContentRepo(session).get_chapter_by_public_id(public_id)
        assert chapter is None
    finally:
        await _cleanup(session)


async def test_get_chapter_by_public_id_returns_none_for_unknown_uuid(
    session: AsyncSession,
) -> None:
    chapter = await ContentRepo(session).get_chapter_by_public_id(
        UUID("00000000-0000-4000-8000-000000000000")
    )
    assert chapter is None


# ---------------------------------------------------------------------------
# Q-3 — get_season_by_slug
# ---------------------------------------------------------------------------


async def test_get_season_by_slug_returns_with_counts(session: AsyncSession) -> None:
    """Two live + one archived + one ready ⇒ chapter_count=3, current_day=2."""
    await _deactivate_all_seasons(session)
    try:
        season_id = await _insert_season(
            session,
            slug=_slug("season-001"),
            title="Season with counts",
            bible={"setting": "Test City"},
        )
        # Two live chapters, day 1 and 2.
        await _insert_chapter(
            session,
            season_id=season_id,
            day_index=1,
            status="archived",
            released_at=datetime(2026, 6, 7, 15, 0, tzinfo=UTC),
        )
        await _insert_chapter(
            session,
            season_id=season_id,
            day_index=2,
            status="live",
            released_at=datetime(2026, 6, 9, 15, 0, tzinfo=UTC),
        )
        await _insert_chapter(
            session,
            season_id=season_id,
            day_index=3,
            status="archived",
            released_at=datetime(2026, 6, 8, 15, 0, tzinfo=UTC),
        )
        # One pre-release: must NOT count.
        await _insert_chapter(
            session,
            season_id=season_id,
            day_index=4,
            status="ready",
            released_at=None,
        )
        await session.commit()

        sp = await ContentRepo(session).get_season_by_slug(_slug("season-001"))
        assert sp is not None
        assert isinstance(sp, SeasonPayload)
        assert sp.slug == _slug("season-001")
        assert sp.title == "Season with counts"
        assert sp.bible_json == {"setting": "Test City"}
        assert sp.chapter_count == 3  # 2 archived + 1 live
        assert sp.current_day_index == 2  # MAX(day_index) among live = 2
    finally:
        await _cleanup(session)


async def test_get_season_by_slug_current_day_index_null_without_live_chapters(
    session: AsyncSession,
) -> None:
    await _deactivate_all_seasons(session)
    try:
        season_id = await _insert_season(session, slug=_slug("season-002"))
        await _insert_chapter(
            session,
            season_id=season_id,
            day_index=1,
            status="ready",
            released_at=None,
        )
        await session.commit()

        sp = await ContentRepo(session).get_season_by_slug(_slug("season-002"))
        assert sp is not None
        assert sp.chapter_count == 0  # ready does not count
        assert sp.current_day_index is None
    finally:
        await _cleanup(session)


async def test_get_season_by_slug_returns_none_for_unknown_slug(
    session: AsyncSession,
) -> None:
    sp = await ContentRepo(session).get_season_by_slug("__nope__")
    assert sp is None


# ---------------------------------------------------------------------------
# EXPLAIN ANALYZE — Q-1 must use the uniq_one_active_season partial index
# (per data-model.md "Verdict: no new indexes required" — we lock in usage)
# ---------------------------------------------------------------------------


async def test_q1_uses_active_season_partial_index(session: AsyncSession) -> None:
    """Force the planner to prefer indexes; confirm uniq_one_active_season is
    in the plan. If the partial unique index were dropped, this fails loudly.
    """
    await _deactivate_all_seasons(session)
    try:
        season_id = await _insert_season(session, slug=_slug("q1-plan-001"))
        chapter_id, _ = await _insert_chapter(
            session,
            season_id=season_id,
            day_index=1,
            status="live",
            released_at=datetime(2026, 6, 9, 15, 0, tzinfo=UTC),
        )
        await _insert_cycle(session, season_id=season_id, chapter_id=chapter_id)
        await session.commit()

        await session.execute(sa.text("SET LOCAL enable_seqscan = OFF"))
        result = await session.execute(
            sa.text(
                "EXPLAIN SELECT 1 FROM cycles c "
                "JOIN chapters ch ON ch.id = c.chapter_id "
                "JOIN seasons  s  ON s.id  = c.season_id "
                "WHERE s.is_active = TRUE LIMIT 1"
            )
        )
        plan = "\n".join(str(row[0]) for row in result.all())
        assert "uniq_one_active_season" in plan, plan
    finally:
        await _cleanup(session)
