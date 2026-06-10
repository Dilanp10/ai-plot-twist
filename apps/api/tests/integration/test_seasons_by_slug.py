"""Integration tests: GET /api/v1/seasons/{slug} (module 004 / T-009).

Covers: happy path with bible redaction, 503 kill-switch, 404 not-found,
Cache-Control with swr=3600.

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
from uuid import uuid4

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

_SLUG_PREFIX = "_sbs-test-"
_TODAY = date(2026, 6, 9)


def _alembic_config(database_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


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


async def _set_kill_switch(s: AsyncSession, *, on: bool, reason: str | None = None) -> None:
    flag = {"on": on, "reason": reason}
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


async def _insert_season(
    s: AsyncSession,
    *,
    slug: str,
    title: str = "Season Test",
    bible: dict[str, Any] | None = None,
) -> int:
    await s.execute(sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE"))
    bible = bible if bible is not None else {"setting": "T"}
    r = await s.execute(
        sa.text(
            "INSERT INTO seasons (slug, title, bible_json, started_on, is_active) "
            "VALUES (:s, :t, :b::jsonb, :d, TRUE) RETURNING id"
        ),
        {"s": slug, "t": title, "b": json.dumps(bible), "d": _TODAY},
    )
    return int(r.scalar_one())


async def _insert_chapter(
    s: AsyncSession,
    *,
    season_id: int,
    day_index: int,
    status: str,
    released_at: datetime | None = None,
) -> None:
    await s.execute(
        sa.text(
            "INSERT INTO chapters (public_id, season_id, day_index, title, synopsis, "
            "manifest_json, status, released_at) "
            "VALUES (:pid, :sid, :di, 'T', 'syn', '{}'::jsonb, :status, :ra)"
        ),
        {
            "pid": uuid4(),
            "sid": season_id,
            "di": day_index,
            "status": status,
            "ra": released_at,
        },
    )


async def _cleanup(s: AsyncSession) -> None:
    await s.execute(sa.text(f"DELETE FROM seasons WHERE slug LIKE '{_SLUG_PREFIX}%'"))
    await _set_kill_switch(s, on=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_get_season_by_slug_returns_200_with_redacted_bible(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    bible = {
        "setting": "Buenos Aires 2027",
        "tone": ["drama", "sci-fi"],
        "characters": [{"name": "Val", "archetype": "hero"}],
        "rules": ["AI is ubiquitous"],
        # Private — must NOT appear in response:
        "secrets": "ending X",
        "plot_twists_planned": ["ep5", "ep9"],
    }
    try:
        slug = f"{_SLUG_PREFIX}red"
        sid = await _insert_season(session, slug=slug, title="S Redacted", bible=bible)
        await _insert_chapter(
            session,
            season_id=sid,
            day_index=1,
            status="live",
            released_at=datetime(2026, 6, 9, 15, 0, tzinfo=UTC),
        )
        await session.commit()

        r = await client.get(f"/api/v1/seasons/{slug}")
        assert r.status_code == 200
        body = r.json()
        assert body["season"]["slug"] == slug
        bp = body["season"]["bible_public"]
        assert "secrets" not in bp
        assert "plot_twists_planned" not in bp
        assert bp["setting"] == "Buenos Aires 2027"
        # Counts
        assert body["season"]["chapter_count"] == 1
        assert body["season"]["current_day_index"] == 1
        # Cache headers
        cc = r.headers["Cache-Control"]
        assert "max-age=300" in cc
        assert "stale-while-revalidate=3600" in cc
    finally:
        await _cleanup(session)


async def test_get_season_by_slug_returns_503_when_kill_switch_active(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    try:
        slug = f"{_SLUG_PREFIX}ks"
        await _insert_season(session, slug=slug)
        await session.commit()
        await _set_kill_switch(session, on=True, reason="mantenimiento")

        r = await client.get(f"/api/v1/seasons/{slug}")
        assert r.status_code == 503
        assert r.headers["Cache-Control"] == "no-store"
        assert r.headers["Retry-After"] == "3600"
        body = r.json()
        assert body["code"] == "under_maintenance"
        assert body["reason"] == "mantenimiento"
    finally:
        await _cleanup(session)


async def test_get_season_by_slug_returns_404_for_unknown(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    try:
        r = await client.get("/api/v1/seasons/__nope__")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/problem+json")
        body = r.json()
        assert body["code"] == "season_not_found"
        assert body["slug"] == "__nope__"
    finally:
        await _cleanup(session)


async def test_get_season_by_slug_with_no_live_chapter_has_null_current_day(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    try:
        slug = f"{_SLUG_PREFIX}nlc"
        sid = await _insert_season(session, slug=slug)
        await _insert_chapter(
            session,
            season_id=sid,
            day_index=1,
            status="ready",
            released_at=None,
        )
        await session.commit()

        r = await client.get(f"/api/v1/seasons/{slug}")
        assert r.status_code == 200
        body = r.json()
        assert body["season"]["current_day_index"] is None
        assert body["season"]["chapter_count"] == 0
    finally:
        await _cleanup(session)
