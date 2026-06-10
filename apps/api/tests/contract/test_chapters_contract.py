"""Contract tests: chapters/seasons responses match contracts/chapters.yaml.

Module 004 / Task T-010.

For each public endpoint:
  1. Fire a known-good request via httpx.AsyncClient + ASGITransport.
  2. Look up the response schema in contracts/chapters.yaml.
  3. Validate the JSON body with ``jsonschema.validate``.

If a route ever drifts from the contract (a renamed field, a new required key,
a removed property), this test fails before the change ships. The contract is
the source of truth — modules 005/007/008/010 consume the same YAML.

Skips when DATABASE_URL is the conftest placeholder (no real DB available).

The cross-file ``Problem`` $ref in chapters.yaml points to
``001-project-bootstrap/contracts/health.yaml#/components/schemas/Problem``;
since this test only validates success-path bodies (not error bodies), the
$ref resolver is not needed.
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
import yaml
from alembic.config import Config
from httpx import ASGITransport
from jsonschema import Draft202012Validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.db import get_session
from app.infra.system_flags_repo import clear_cache
from app.main import create_app

API_DIR = Path(__file__).parent.parent.parent
ALEMBIC_INI = API_DIR / "alembic.ini"
REPO_ROOT = API_DIR.parent.parent
CONTRACT_YAML = REPO_ROOT / "specs" / "004-chapters-content" / "contracts" / "chapters.yaml"

_SLUG_PREFIX = "_cct-test-"
_TODAY = date(2026, 6, 9)


def _alembic_config(database_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


# ---------------------------------------------------------------------------
# Contract loader
# ---------------------------------------------------------------------------


def _load_contract() -> dict[str, Any]:
    with CONTRACT_YAML.open(encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)
    assert isinstance(loaded, dict)
    return loaded


def _schema_for(contract: dict[str, Any], name: str) -> dict[str, Any]:
    """Resolve a top-level ``components.schemas.<name>`` block, inlining $ref
    references to siblings within the same components.schemas namespace.

    Cross-file $refs (Problem from health.yaml) are left as-is; this test only
    validates success bodies so they are not exercised.
    """
    schemas = contract["components"]["schemas"]
    schema = schemas[name]
    inlined = _inline_local_refs(schema, schemas)
    assert isinstance(inlined, dict)
    return inlined


def _inline_local_refs(node: Any, schemas: dict[str, Any]) -> Any:
    """Recursively replace ``$ref: '#/components/schemas/Foo'`` with the
    actual schema dict. Cross-file refs are left as a stub object so
    ``jsonschema`` ignores them rather than failing on resolution.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            if ref.startswith("#/components/schemas/"):
                target = ref.rsplit("/", 1)[-1]
                inner = schemas.get(target)
                if inner is None:
                    return {}
                return _inline_local_refs(inner, schemas)
            # Cross-file ref (e.g. ../../001-.../health.yaml#/...) — return
            # an empty object so jsonschema does not try to resolve it.
            return {}
        return {k: _inline_local_refs(v, schemas) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_local_refs(v, schemas) for v in node]
    return node


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


@pytest.fixture(scope="module")
def contract() -> dict[str, Any]:
    return _load_contract()


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


async def _seed_live(s: AsyncSession, *, slug: str) -> UUID:
    await s.execute(sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE"))
    r = await s.execute(
        sa.text(
            "INSERT INTO seasons (slug, title, bible_json, started_on, is_active) "
            "VALUES (:s, 'Test Season', :b::jsonb, :d, TRUE) RETURNING id"
        ),
        {
            "s": slug,
            "b": json.dumps(
                {
                    "setting": "Test City",
                    "tone": ["drama"],
                    "characters": [{"name": "Hero", "archetype": "protagonist"}],
                    "rules": ["No magic"],
                    "secrets": "kept private",
                }
            ),
            "d": _TODAY,
        },
    )
    sid = int(r.scalar_one())
    public_id = uuid4()
    manifest = {
        "panels": [
            {
                "idx": 1,
                "image_url": "https://x.test/1.webp",
                "image_blurhash": "BH",
                "tts_url": "https://x.test/1.mp3",
                "narration": "Empezó la historia.",
                "mood": "calm",
            }
        ],
        "cliffhanger": "Una voz desconocida...",
    }
    r = await s.execute(
        sa.text(
            "INSERT INTO chapters (public_id, season_id, day_index, title, synopsis, "
            "manifest_json, status, released_at) "
            "VALUES (:pid, :sid, 1, 'Day 1', 'syn', :m::jsonb, 'live', :ra) RETURNING id"
        ),
        {
            "pid": public_id,
            "sid": sid,
            "m": json.dumps(manifest),
            "ra": datetime(2026, 6, 9, 15, 0, tzinfo=UTC),
        },
    )
    cid = int(r.scalar_one())
    await s.execute(
        sa.text(
            "INSERT INTO cycles (season_id, chapter_id, state, state_entered_at, cycle_date) "
            "VALUES (:sid, :cid, 'RECEPCION_IDEAS', :sea, :cd)"
        ),
        {"sid": sid, "cid": cid, "sea": datetime(2026, 6, 9, 15, 0, tzinfo=UTC), "cd": _TODAY},
    )
    await s.commit()
    return public_id


async def _cleanup(s: AsyncSession) -> None:
    await s.execute(sa.text(f"DELETE FROM seasons WHERE slug LIKE '{_SLUG_PREFIX}%'"))
    await s.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_chapters_today_response_matches_contract(
    client: httpx.AsyncClient,
    session: AsyncSession,
    contract: dict[str, Any],
) -> None:
    try:
        await _seed_live(session, slug=f"{_SLUG_PREFIX}today")
        r = await client.get("/api/v1/chapters/today")
        assert r.status_code == 200, r.text

        schema = _schema_for(contract, "TodayResponse")
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(r.json()), key=lambda e: e.path)
        assert not errors, [f"{list(e.path)}: {e.message}" for e in errors]
    finally:
        await _cleanup(session)


async def test_chapter_by_id_response_matches_contract(
    client: httpx.AsyncClient,
    session: AsyncSession,
    contract: dict[str, Any],
) -> None:
    try:
        public_id = await _seed_live(session, slug=f"{_SLUG_PREFIX}byid")
        r = await client.get(f"/api/v1/chapters/{public_id}")
        assert r.status_code == 200, r.text

        schema = _schema_for(contract, "ChapterResponse")
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(r.json()), key=lambda e: e.path)
        assert not errors, [f"{list(e.path)}: {e.message}" for e in errors]
    finally:
        await _cleanup(session)


async def test_season_by_slug_response_matches_contract(
    client: httpx.AsyncClient,
    session: AsyncSession,
    contract: dict[str, Any],
) -> None:
    try:
        slug = f"{_SLUG_PREFIX}season"
        await _seed_live(session, slug=slug)
        r = await client.get(f"/api/v1/seasons/{slug}")
        assert r.status_code == 200, r.text

        schema = _schema_for(contract, "SeasonResponse")
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(r.json()), key=lambda e: e.path)
        assert not errors, [f"{list(e.path)}: {e.message}" for e in errors]
    finally:
        await _cleanup(session)
