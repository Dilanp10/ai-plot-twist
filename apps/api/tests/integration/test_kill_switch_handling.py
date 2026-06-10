"""Integration tests: kill-switch handling on /chapters/today (module 004 / T-007).

When ``system_flags.kill_switch.on = TRUE``:
  * /chapters/today MUST return 503 under_maintenance with Retry-After and
    Cache-Control: no-store.
  * The response body MUST include the kill-switch ``reason`` so the PWA can
    display it.

Skips when DATABASE_URL is the conftest placeholder.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path
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

_SLUG_PREFIX = "_ks-test-"
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


async def _seed_live_today(session: AsyncSession, slug: str) -> None:
    await session.execute(sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE"))
    r = await session.execute(
        sa.text(
            "INSERT INTO seasons (slug, title, bible_json, started_on, is_active) "
            "VALUES (:s, 'T', '{}'::jsonb, :d, TRUE) RETURNING id"
        ),
        {"s": slug, "d": _TODAY},
    )
    sid = int(r.scalar_one())
    r = await session.execute(
        sa.text(
            "INSERT INTO chapters (public_id, season_id, day_index, title, synopsis, "
            "manifest_json, status, released_at) "
            "VALUES (:pid, :sid, 1, 'T', 'syn', :m::jsonb, 'live', :ra) RETURNING id"
        ),
        {
            "pid": uuid4(),
            "sid": sid,
            "m": json.dumps({"panels": [], "cliffhanger": "..."}),
            "ra": datetime(2026, 6, 9, 15, 0, tzinfo=UTC),
        },
    )
    cid = int(r.scalar_one())
    await session.execute(
        sa.text(
            "INSERT INTO cycles (season_id, chapter_id, state, state_entered_at, cycle_date) "
            "VALUES (:sid, :cid, 'RECEPCION_IDEAS', :sea, :cd)"
        ),
        {
            "sid": sid,
            "cid": cid,
            "sea": datetime(2026, 6, 9, 15, 0, tzinfo=UTC),
            "cd": _TODAY,
        },
    )
    await session.commit()


async def _cleanup(s: AsyncSession) -> None:
    await s.execute(sa.text(f"DELETE FROM seasons WHERE slug LIKE '{_SLUG_PREFIX}%'"))
    await _set_kill_switch(s, on=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_today_returns_503_under_maintenance_with_retry_after(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Kill-switch ON precedes the data query — even with a healthy cycle, 503."""
    try:
        await _seed_live_today(session, slug=f"{_SLUG_PREFIX}ks-001")
        await _set_kill_switch(session, on=True, reason="ajustando la bible")

        r = await client.get("/api/v1/chapters/today")
        assert r.status_code == 503
        assert r.headers["content-type"].startswith("application/problem+json")
        assert r.headers["Cache-Control"] == "no-store"
        assert r.headers["Retry-After"] == "3600"

        body = r.json()
        assert body["code"] == "under_maintenance"
        assert body["status"] == 503
        assert body["reason"] == "ajustando la bible"
        assert body["retry_after_seconds"] == 3600
    finally:
        await _cleanup(session)


async def test_today_returns_503_under_maintenance_with_null_reason(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    try:
        await _seed_live_today(session, slug=f"{_SLUG_PREFIX}ks-002")
        await _set_kill_switch(session, on=True, reason=None)

        r = await client.get("/api/v1/chapters/today")
        assert r.status_code == 503
        body = r.json()
        assert body["code"] == "under_maintenance"
        assert body["reason"] is None
    finally:
        await _cleanup(session)


async def test_today_recovers_after_kill_switch_off(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """Once kill-switch is off, normal 200 resumes."""
    try:
        await _seed_live_today(session, slug=f"{_SLUG_PREFIX}ks-rec")
        await _set_kill_switch(session, on=True, reason="brb")
        r_off = await client.get("/api/v1/chapters/today")
        assert r_off.status_code == 503

        await _set_kill_switch(session, on=False)
        r_on = await client.get("/api/v1/chapters/today")
        assert r_on.status_code == 200
        assert "ETag" in r_on.headers
    finally:
        await _cleanup(session)
