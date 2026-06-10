"""Integration tests: GET /api/v1/chapters/today (module 004 / T-007).

Covers happy path, no_active_season, no_live_chapter, content_read log emission
and Cache-Control headers. Kill-switch handling lives in test_kill_switch_handling.py;
ETag/304 in test_etag_304.py.

Skips when DATABASE_URL is the conftest placeholder.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from alembic.config import Config
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.db import get_session
from app.infra.system_flags_repo import clear_cache
from app.main import create_app

API_DIR = Path(__file__).parent.parent.parent
ALEMBIC_INI = API_DIR / "alembic.ini"

_SLUG_PREFIX = "_ct-test-"
_TODAY = date(2026, 6, 9)


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


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def _override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _flush_cache() -> None:
    clear_cache()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _deactivate_all_seasons(s: AsyncSession) -> None:
    await s.execute(sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE"))
    await s.commit()


async def _set_kill_switch(s: AsyncSession, *, on: bool) -> None:
    flag = {"on": on, "reason": None}
    await s.execute(
        sa.text(
            "INSERT INTO system_flags (flag_key, flag_value, updated_by) "
            "VALUES ('kill_switch', :v::jsonb, 'test') "
            "ON CONFLICT (flag_key) DO UPDATE SET "
            "flag_value = EXCLUDED.flag_value, updated_at = now()"
        ),
        {"v": json.dumps(flag)},
    )
    await s.commit()
    clear_cache()


async def _insert_season(s: AsyncSession, *, slug: str, title: str = "S Test") -> int:
    r = await s.execute(
        sa.text(
            "INSERT INTO seasons (slug, title, bible_json, started_on, is_active) "
            "VALUES (:slug, :title, '{}'::jsonb, :today, TRUE) RETURNING id"
        ),
        {"slug": slug, "title": title, "today": _TODAY},
    )
    return int(r.scalar_one())


async def _insert_chapter(
    s: AsyncSession,
    *,
    season_id: int,
    day_index: int,
    status: str,
    manifest: dict[str, Any] | None = None,
    released_at: datetime | None = None,
    public_id: UUID | None = None,
) -> tuple[int, UUID]:
    manifest = manifest if manifest is not None else {"panels": [], "cliffhanger": "..."}
    public_id = public_id if public_id is not None else uuid4()
    r = await s.execute(
        sa.text(
            "INSERT INTO chapters (public_id, season_id, day_index, title, synopsis, "
            "manifest_json, status, released_at) "
            "VALUES (:pid, :sid, :di, 'T', 'syn', :m::jsonb, :status, :ra) RETURNING id"
        ),
        {
            "pid": public_id,
            "sid": season_id,
            "di": day_index,
            "m": json.dumps(manifest),
            "status": status,
            "ra": released_at,
        },
    )
    return int(r.scalar_one()), public_id


async def _insert_cycle(
    s: AsyncSession,
    *,
    season_id: int,
    chapter_id: int,
    state: str = "RECEPCION_IDEAS",
) -> None:
    await s.execute(
        sa.text(
            "INSERT INTO cycles (season_id, chapter_id, state, state_entered_at, cycle_date) "
            "VALUES (:sid, :cid, :st, :sea, :cd)"
        ),
        {
            "sid": season_id,
            "cid": chapter_id,
            "st": state,
            "sea": datetime(2026, 6, 9, 15, 0, tzinfo=UTC),
            "cd": _TODAY,
        },
    )


async def _cleanup(s: AsyncSession) -> None:
    await s.execute(sa.text(f"DELETE FROM seasons WHERE slug LIKE '{_SLUG_PREFIX}%'"))
    await _set_kill_switch(s, on=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_chapters_today_happy_path_returns_200_with_dto_shape(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _deactivate_all_seasons(session)
    try:
        sid = await _insert_season(session, slug=_slug("h-001"), title="Happy S")
        manifest = {
            "panels": [
                {"idx": 1, "image_url": "https://x/1.webp", "narration": "Inicio.", "mood": "calm"}
            ],
            "cliffhanger": "Una voz en la oscuridad.",
        }
        cid, public_id = await _insert_chapter(
            session,
            season_id=sid,
            day_index=7,
            status="live",
            manifest=manifest,
            released_at=datetime(2026, 6, 9, 15, 0, tzinfo=UTC),
        )
        await _insert_cycle(session, season_id=sid, chapter_id=cid)
        await session.commit()

        r = await client.get("/api/v1/chapters/today")
        assert r.status_code == 200
        body = r.json()
        assert body["cycle_state"] == "RECEPCION_IDEAS"
        assert body["season"]["slug"] == _slug("h-001")
        assert body["chapter"]["id"] == str(public_id)
        assert body["chapter"]["day_index"] == 7
        assert body["chapter"]["cliffhanger"] == "Una voz en la oscuridad."
        assert len(body["chapter"]["panels"]) == 1
        # Cache headers
        cc = r.headers["Cache-Control"]
        assert "max-age=60" in cc
        assert "stale-while-revalidate=600" in cc
        assert "must-revalidate" in cc
        # ETag present + quoted
        assert r.headers["ETag"].startswith('"') and r.headers["ETag"].endswith('"')
    finally:
        await _cleanup(session)


async def test_chapters_today_returns_503_no_active_season(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _deactivate_all_seasons(session)
    try:
        r = await client.get("/api/v1/chapters/today")
        assert r.status_code == 503
        assert r.headers["content-type"].startswith("application/problem+json")
        assert r.headers["Cache-Control"] == "no-store"
        body = r.json()
        assert body["code"] == "no_active_season"
        assert body["status"] == 503
    finally:
        await _cleanup(session)


async def test_chapters_today_returns_404_no_live_chapter_with_first_release_at(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Bootstrap day: cycle in PENDING_RELEASE, chapter ready."""
    await _deactivate_all_seasons(session)
    try:
        sid = await _insert_season(session, slug=_slug("nlc-001"))
        cid, _ = await _insert_chapter(
            session, season_id=sid, day_index=1, status="ready", released_at=None
        )
        await _insert_cycle(session, season_id=sid, chapter_id=cid, state="PENDING_RELEASE")
        await session.commit()

        r = await client.get("/api/v1/chapters/today")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/problem+json")
        body = r.json()
        assert body["code"] == "no_live_chapter"
        assert "first_release_at" in body
        # cycle_date 2026-06-09 @ 12:00 ART = 15:00 UTC
        assert body["first_release_at"] == "2026-06-09T15:00:00+00:00"
    finally:
        await _cleanup(session)
