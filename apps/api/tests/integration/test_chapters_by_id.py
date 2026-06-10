"""Integration tests: GET /api/v1/chapters/{public_id} (module 004 / T-008).

Covers: live & archived 200 paths, 404 for unknown/pre-release, 503 for
kill-switch, ETag round-trip (304).

Skips when DATABASE_URL is the conftest placeholder.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path
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

_SLUG_PREFIX = "_cbi-test-"
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


async def _insert_chapter_with_season(
    s: AsyncSession,
    *,
    slug: str,
    status: str,
    released_at: datetime | None = None,
) -> UUID:
    await s.execute(sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE"))
    r = await s.execute(
        sa.text(
            "INSERT INTO seasons (slug, title, bible_json, started_on, is_active) "
            "VALUES (:s, 'T', '{}'::jsonb, :d, TRUE) RETURNING id"
        ),
        {"s": slug, "d": _TODAY},
    )
    sid = int(r.scalar_one())
    public_id = uuid4()
    await s.execute(
        sa.text(
            "INSERT INTO chapters (public_id, season_id, day_index, title, synopsis, "
            "manifest_json, status, released_at) "
            "VALUES (:pid, :sid, 1, 'T', 'syn', :m::jsonb, :status, :ra)"
        ),
        {
            "pid": public_id,
            "sid": sid,
            "m": json.dumps({"panels": [], "cliffhanger": "..."}),
            "status": status,
            "ra": released_at,
        },
    )
    await s.commit()
    return public_id


async def _cleanup(s: AsyncSession) -> None:
    await s.execute(sa.text(f"DELETE FROM seasons WHERE slug LIKE '{_SLUG_PREFIX}%'"))
    await _set_kill_switch(s, on=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_get_chapter_by_id_returns_200_live(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    try:
        public_id = await _insert_chapter_with_season(
            session,
            slug=f"{_SLUG_PREFIX}live",
            status="live",
            released_at=datetime(2026, 6, 9, 15, 0, tzinfo=UTC),
        )
        r = await client.get(f"/api/v1/chapters/{public_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["chapter"]["id"] == str(public_id)
        assert body["season"]["slug"] == f"{_SLUG_PREFIX}live"
        # Live chapter cache: short-fresh + swr (NOT immutable per Decision in chapters.py).
        cc = r.headers["Cache-Control"]
        assert "max-age=60" in cc
        assert "stale-while-revalidate=600" in cc
        assert "immutable" not in cc
        # ETag present.
        assert r.headers["ETag"].startswith('"')
    finally:
        await _cleanup(session)


async def test_get_chapter_by_id_returns_200_archived(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    try:
        public_id = await _insert_chapter_with_season(
            session,
            slug=f"{_SLUG_PREFIX}arch",
            status="archived",
            released_at=datetime(2026, 6, 5, 15, 0, tzinfo=UTC),
        )
        r = await client.get(f"/api/v1/chapters/{public_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["chapter"]["id"] == str(public_id)
    finally:
        await _cleanup(session)


async def test_get_chapter_by_id_returns_404_for_unknown_uuid(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    try:
        unknown = "00000000-0000-4000-8000-000000000000"
        r = await client.get(f"/api/v1/chapters/{unknown}")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/problem+json")
        body = r.json()
        assert body["code"] == "chapter_not_found"
        assert body["public_id"] == unknown
    finally:
        await _cleanup(session)


@pytest.mark.parametrize("pre_status", ["draft", "generating", "ready", "ready_degraded"])
async def test_get_chapter_by_id_returns_404_for_pre_release(
    client: httpx.AsyncClient, session: AsyncSession, pre_status: str
) -> None:
    try:
        public_id = await _insert_chapter_with_season(
            session,
            slug=f"{_SLUG_PREFIX}{pre_status}",
            status=pre_status,
            released_at=None,
        )
        r = await client.get(f"/api/v1/chapters/{public_id}")
        assert r.status_code == 404
        body = r.json()
        assert body["code"] == "chapter_not_found"
    finally:
        await _cleanup(session)


async def test_get_chapter_by_id_returns_503_when_kill_switch_active(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    try:
        public_id = await _insert_chapter_with_season(
            session,
            slug=f"{_SLUG_PREFIX}ks",
            status="live",
            released_at=datetime(2026, 6, 9, 15, 0, tzinfo=UTC),
        )
        await _set_kill_switch(session, on=True, reason="mantenimiento")
        r = await client.get(f"/api/v1/chapters/{public_id}")
        assert r.status_code == 503
        assert r.headers["Retry-After"] == "3600"
        body = r.json()
        assert body["code"] == "under_maintenance"
        assert body["reason"] == "mantenimiento"
    finally:
        await _cleanup(session)


async def test_get_chapter_by_id_304_on_if_none_match(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    try:
        public_id = await _insert_chapter_with_season(
            session,
            slug=f"{_SLUG_PREFIX}etag",
            status="archived",
            released_at=datetime(2026, 6, 5, 15, 0, tzinfo=UTC),
        )
        first = await client.get(f"/api/v1/chapters/{public_id}")
        assert first.status_code == 200
        etag = first.headers["ETag"]

        second = await client.get(f"/api/v1/chapters/{public_id}", headers={"If-None-Match": etag})
        assert second.status_code == 304
        assert second.content == b""
        assert second.headers["ETag"] == etag
    finally:
        await _cleanup(session)
