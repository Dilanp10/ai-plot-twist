"""Integration tests: ContentService (module 004 / T-005).

Skips when DATABASE_URL is the conftest placeholder.

Each test seeds its own season/chapter/cycle + kill-switch state via direct
SQL. The 30 s in-process flag cache is flushed before *and* after each test
so kill-switch state from one test never leaks into the next.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.domain.content_service import (
    ChapterDTO,
    ChapterNotFound,
    ChapterResponseDTO,
    ContentService,
    KillSwitchActive,
    NoActiveSeason,
    NoLiveChapter,
    SeasonNotFound,
    SeasonResponseDTO,
    TodayResponseDTO,
)
from app.domain.windows import CycleTimes
from app.infra.content_repo import ContentRepo
from app.infra.system_flags_repo import SystemFlagsRepo, clear_cache

API_DIR = Path(__file__).parent.parent.parent
ALEMBIC_INI = API_DIR / "alembic.ini"

_SLUG_PREFIX = "_cs-test-"
_TODAY = date(2026, 6, 9)
_FIXED_NOW = datetime(2026, 6, 9, 14, 0, tzinfo=UTC)  # 11:00 ART


def _alembic_config(database_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _slug(suffix: str) -> str:
    return f"{_SLUG_PREFIX}{suffix}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


@pytest.fixture(autouse=True)
def _flush_flag_cache() -> Iterator[None]:
    clear_cache()
    yield
    clear_cache()


def _make_service(session: AsyncSession) -> ContentService:
    return ContentService(
        content_repo=ContentRepo(session),
        flags_repo=SystemFlagsRepo(session),
        cycle_times=CycleTimes.default(),
        now_utc=lambda: _FIXED_NOW,
    )


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _deactivate_all_seasons(s: AsyncSession) -> None:
    await s.execute(sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE"))
    await s.commit()


async def _insert_season(
    s: AsyncSession,
    *,
    slug: str,
    title: str = "Season Test",
    bible: dict[str, Any] | None = None,
    started_on: date = _TODAY,
    is_active: bool = True,
) -> int:
    bible = bible if bible is not None else {"setting": "Test City"}
    result = await s.execute(
        sa.text(
            "INSERT INTO seasons (slug, title, bible_json, started_on, is_active) "
            "VALUES (:slug, :title, :bible::jsonb, :started_on, :is_active) RETURNING id"
        ),
        {
            "slug": slug,
            "title": title,
            "bible": json.dumps(bible),
            "started_on": started_on,
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
    manifest: dict[str, Any] | None = None,
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
            "INSERT INTO cycles (season_id, chapter_id, state, state_entered_at, cycle_date) "
            "VALUES (:season_id, :chapter_id, :state, :state_entered_at, :cycle_date) RETURNING id"
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


async def _set_kill_switch(s: AsyncSession, *, on: bool, reason: str | None = None) -> None:
    flag = {"on": on, "reason": reason}
    await s.execute(
        sa.text(
            "INSERT INTO system_flags (flag_key, flag_value, updated_by) "
            "VALUES ('kill_switch', :v::jsonb, 'test') "
            "ON CONFLICT (flag_key) DO UPDATE SET "
            "  flag_value = EXCLUDED.flag_value, updated_at = now()"
        ),
        {"v": json.dumps(flag)},
    )
    await s.commit()
    clear_cache()


async def _cleanup(s: AsyncSession) -> None:
    await s.execute(sa.text(f"DELETE FROM seasons WHERE slug LIKE '{_SLUG_PREFIX}%'"))
    await _set_kill_switch(s, on=False)
    await s.commit()


# ---------------------------------------------------------------------------
# today()
# ---------------------------------------------------------------------------


async def test_today_happy_path(session: AsyncSession) -> None:
    await _deactivate_all_seasons(session)
    try:
        season_id = await _insert_season(
            session,
            slug=_slug("today-001"),
            title="Today Test",
        )
        manifest = {
            "panels": [
                {
                    "idx": 1,
                    "image_url": "https://assets/x/1.webp",
                    "image_blurhash": "BH",
                    "tts_url": "https://assets/x/1.mp3",
                    "narration": "Empezó la historia.",
                    "mood": "calm",
                },
                {
                    "idx": 2,
                    "image_url": "https://assets/x/2.webp",
                    "narration": "Y después se complicó.",
                    "mood": "tense",
                },
            ],
            "cliffhanger": "¿Qué hará Valentina?",
        }
        chapter_id, public_id = await _insert_chapter(
            session,
            season_id=season_id,
            day_index=7,
            status="live",
            title="El día 7",
            synopsis="Mariana cruza el umbral.",
            manifest=manifest,
            released_at=datetime(2026, 6, 9, 15, 0, tzinfo=UTC),
        )
        await _insert_cycle(
            session,
            season_id=season_id,
            chapter_id=chapter_id,
            state="RECEPCION_IDEAS",
        )
        await session.commit()

        dto = await _make_service(session).today()
        assert isinstance(dto, TodayResponseDTO)
        assert dto.cycle_state == "RECEPCION_IDEAS"
        assert dto.season.slug == _slug("today-001")
        assert dto.chapter.id == public_id
        assert dto.chapter.day_index == 7
        assert dto.chapter.title == "El día 7"
        assert dto.chapter.cliffhanger == "¿Qué hará Valentina?"
        assert len(dto.chapter.panels) == 2
        assert dto.chapter.panels[0].image_blurhash == "BH"
        assert dto.chapter.panels[1].image_blurhash is None
        # Windows: submit_until = 2026-06-09 18:00 ART = 21:00 UTC.
        assert dto.windows.submit_until == datetime(2026, 6, 9, 21, 0, tzinfo=UTC)
    finally:
        await _cleanup(session)


async def test_today_raises_kill_switch_active(session: AsyncSession) -> None:
    await _deactivate_all_seasons(session)
    try:
        await _set_kill_switch(session, on=True, reason="ajustando la bible")
        with pytest.raises(KillSwitchActive) as ei:
            await _make_service(session).today()
        assert ei.value.reason == "ajustando la bible"
    finally:
        await _cleanup(session)


async def test_today_raises_no_active_season(session: AsyncSession) -> None:
    await _deactivate_all_seasons(session)
    try:
        with pytest.raises(NoActiveSeason):
            await _make_service(session).today()
    finally:
        await _cleanup(session)


async def test_today_raises_no_live_chapter_with_first_release_at(
    session: AsyncSession,
) -> None:
    """Bootstrap state: cycle in PENDING_RELEASE, chapter status=ready."""
    await _deactivate_all_seasons(session)
    try:
        season_id = await _insert_season(session, slug=_slug("today-nlc"))
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

        with pytest.raises(NoLiveChapter) as ei:
            await _make_service(session).today()
        # 12:00 ART of cycle_date (2026-06-09) = 15:00 UTC
        assert ei.value.first_release_at == datetime(2026, 6, 9, 15, 0, tzinfo=UTC)
    finally:
        await _cleanup(session)


async def test_today_with_missing_panels_returns_empty_list(
    session: AsyncSession,
) -> None:
    """Manifest without 'panels' key → DTO panels = []. Tolerant per spec."""
    await _deactivate_all_seasons(session)
    try:
        season_id = await _insert_season(session, slug=_slug("today-nop"))
        chapter_id, _ = await _insert_chapter(
            session,
            season_id=season_id,
            day_index=1,
            status="live",
            manifest={"cliffhanger": "solo eso"},  # NO panels key
            released_at=datetime(2026, 6, 9, 15, 0, tzinfo=UTC),
        )
        await _insert_cycle(session, season_id=season_id, chapter_id=chapter_id)
        await session.commit()

        dto = await _make_service(session).today()
        assert dto.chapter.panels == []
        assert dto.chapter.cliffhanger == "solo eso"
    finally:
        await _cleanup(session)


# ---------------------------------------------------------------------------
# chapter(public_id)
# ---------------------------------------------------------------------------


async def test_chapter_happy_path(session: AsyncSession) -> None:
    await _deactivate_all_seasons(session)
    try:
        season_id = await _insert_season(session, slug=_slug("chap-001"), title="Chap Test")
        _, public_id = await _insert_chapter(
            session,
            season_id=season_id,
            day_index=3,
            status="archived",
            title="El día 3",
            released_at=datetime(2026, 6, 5, 15, 0, tzinfo=UTC),
        )
        await session.commit()

        dto = await _make_service(session).chapter(public_id)
        assert isinstance(dto, ChapterResponseDTO)
        assert isinstance(dto.chapter, ChapterDTO)
        assert dto.chapter.id == public_id
        assert dto.chapter.day_index == 3
        assert dto.chapter.title == "El día 3"
        assert dto.season.slug == _slug("chap-001")
        assert dto.season.title == "Chap Test"
    finally:
        await _cleanup(session)


async def test_chapter_raises_kill_switch_active(session: AsyncSession) -> None:
    await _deactivate_all_seasons(session)
    try:
        season_id = await _insert_season(session, slug=_slug("chap-ks"))
        _, public_id = await _insert_chapter(
            session,
            season_id=season_id,
            day_index=1,
            status="live",
            released_at=datetime(2026, 6, 9, 15, 0, tzinfo=UTC),
        )
        await session.commit()
        await _set_kill_switch(session, on=True, reason="mantenimiento")

        with pytest.raises(KillSwitchActive):
            await _make_service(session).chapter(public_id)
    finally:
        await _cleanup(session)


async def test_chapter_raises_chapter_not_found_for_unknown_uuid(
    session: AsyncSession,
) -> None:
    unknown = UUID("00000000-0000-4000-8000-000000000000")
    with pytest.raises(ChapterNotFound) as ei:
        await _make_service(session).chapter(unknown)
    assert ei.value.public_id == unknown


async def test_chapter_raises_chapter_not_found_for_pre_release(
    session: AsyncSession,
) -> None:
    """status='ready' is invisible via Q-2 → ChapterNotFound from the service."""
    await _deactivate_all_seasons(session)
    try:
        season_id = await _insert_season(session, slug=_slug("chap-pre"))
        _, public_id = await _insert_chapter(
            session,
            season_id=season_id,
            day_index=1,
            status="ready",
            released_at=None,
        )
        await session.commit()

        with pytest.raises(ChapterNotFound):
            await _make_service(session).chapter(public_id)
    finally:
        await _cleanup(session)


# ---------------------------------------------------------------------------
# season(slug)
# ---------------------------------------------------------------------------


async def test_season_happy_path_redacts_bible(session: AsyncSession) -> None:
    bible_private = {
        "setting": "Buenos Aires 2027",
        "tone": ["drama", "sci-fi"],
        "characters": [{"name": "Val", "archetype": "hero"}],
        "rules": ["AI is ubiquitous"],
        "secrets": "final reveal X",  # MUST be excluded
        "plot_twists_planned": ["ep5", "ep9"],  # MUST be excluded
    }
    await _deactivate_all_seasons(session)
    try:
        season_id = await _insert_season(
            session,
            slug=_slug("season-001"),
            title="S01",
            bible=bible_private,
        )
        await _insert_chapter(
            session,
            season_id=season_id,
            day_index=1,
            status="live",
            released_at=datetime(2026, 6, 5, 15, 0, tzinfo=UTC),
        )
        await session.commit()

        dto = await _make_service(session).season(_slug("season-001"))
        assert isinstance(dto, SeasonResponseDTO)
        assert dto.season.slug == _slug("season-001")
        assert "secrets" not in dto.season.bible_public
        assert "plot_twists_planned" not in dto.season.bible_public
        assert dto.season.bible_public["setting"] == "Buenos Aires 2027"
        assert dto.season.chapter_count == 1
        assert dto.season.current_day_index == 1
    finally:
        await _cleanup(session)


async def test_season_raises_kill_switch_active(session: AsyncSession) -> None:
    await _deactivate_all_seasons(session)
    try:
        await _insert_season(session, slug=_slug("season-ks"))
        await _set_kill_switch(session, on=True, reason="up later")

        with pytest.raises(KillSwitchActive):
            await _make_service(session).season(_slug("season-ks"))
    finally:
        await _cleanup(session)


async def test_season_raises_season_not_found(session: AsyncSession) -> None:
    with pytest.raises(SeasonNotFound) as ei:
        await _make_service(session).season("__nope__")
    assert ei.value.slug == "__nope__"


async def test_season_with_null_current_day_index(session: AsyncSession) -> None:
    """Season has only pre-release chapters → current_day_index is None."""
    await _deactivate_all_seasons(session)
    try:
        season_id = await _insert_season(session, slug=_slug("season-no-live"))
        await _insert_chapter(
            session,
            season_id=season_id,
            day_index=1,
            status="ready",
            released_at=None,
        )
        await session.commit()

        dto = await _make_service(session).season(_slug("season-no-live"))
        assert dto.season.current_day_index is None
        assert dto.season.chapter_count == 0
    finally:
        await _cleanup(session)
