"""Integration tests: If-None-Match → 304 for GET /chapters/today (T-007).

Verifies the conditional GET path:
  1. First call → 200 + ETag.
  2. Second call with If-None-Match: <etag> → 304, empty body, same ETag.
  3. After a cycle state change → ETag rotates → second call again 200.

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

_SLUG_PREFIX = "_et-test-"
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


async def _seed(session: AsyncSession, *, slug: str, state: str = "RECEPCION_IDEAS") -> None:
    await session.execute(sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE"))
    r = await session.execute(
        sa.text(
            "INSERT INTO seasons (slug, title, bible_json, started_on, is_active) "
            "VALUES (:s, 'T', '{}'::jsonb, :d, TRUE) RETURNING id"
        ),
        {"s": slug, "d": _TODAY},
    )
    sid = int(r.scalar_one())
    pid = uuid4()
    r = await session.execute(
        sa.text(
            "INSERT INTO chapters (public_id, season_id, day_index, title, synopsis, "
            "manifest_json, status, released_at) "
            "VALUES (:pid, :sid, 1, 'T', 'syn', :m::jsonb, 'live', :ra) RETURNING id"
        ),
        {
            "pid": pid,
            "sid": sid,
            "m": json.dumps({"panels": [], "cliffhanger": "..."}),
            "ra": datetime(2026, 6, 9, 15, 0, tzinfo=UTC),
        },
    )
    cid = int(r.scalar_one())
    await session.execute(
        sa.text(
            "INSERT INTO cycles (season_id, chapter_id, state, state_entered_at, cycle_date) "
            "VALUES (:sid, :cid, :st, :sea, :cd)"
        ),
        {
            "sid": sid,
            "cid": cid,
            "st": state,
            "sea": datetime(2026, 6, 9, 15, 0, tzinfo=UTC),
            "cd": _TODAY,
        },
    )
    await session.commit()


async def _cleanup(session: AsyncSession) -> None:
    await session.execute(sa.text(f"DELETE FROM seasons WHERE slug LIKE '{_SLUG_PREFIX}%'"))
    await session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_if_none_match_matching_returns_304(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _seed(session, slug=f"{_SLUG_PREFIX}match")
    try:
        first = await client.get("/api/v1/chapters/today")
        assert first.status_code == 200
        etag = first.headers["ETag"]

        second = await client.get("/api/v1/chapters/today", headers={"If-None-Match": etag})
        assert second.status_code == 304
        assert second.content == b""
        assert second.headers["ETag"] == etag
        # Cache headers still set even on 304 so caches can refresh their max-age.
        assert "max-age=60" in second.headers["Cache-Control"]
    finally:
        await _cleanup(session)


async def test_if_none_match_non_matching_returns_200(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _seed(session, slug=f"{_SLUG_PREFIX}miss")
    try:
        r = await client.get(
            "/api/v1/chapters/today",
            headers={"If-None-Match": '"0000000000000000"'},
        )
        assert r.status_code == 200
        assert r.headers["ETag"] != '"0000000000000000"'
    finally:
        await _cleanup(session)


async def test_etag_changes_when_cycle_state_changes(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    """ETag derives from cycle_state, so a state change must rotate it."""
    await _seed(session, slug=f"{_SLUG_PREFIX}rot", state="RECEPCION_IDEAS")
    try:
        first = await client.get("/api/v1/chapters/today")
        first_etag = first.headers["ETag"]

        # Manually advance the cycle state in DB.
        await session.execute(
            sa.text(
                f"UPDATE cycles SET state = 'VOTACION' "
                f"WHERE season_id IN (SELECT id FROM seasons WHERE slug LIKE '{_SLUG_PREFIX}rot')"
            )
        )
        await session.commit()

        second = await client.get("/api/v1/chapters/today")
        assert second.headers["ETag"] != first_etag
    finally:
        await _cleanup(session)
