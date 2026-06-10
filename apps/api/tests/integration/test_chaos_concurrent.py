"""Integration test: concurrent-tick chaos (T-024).

Spawns 50 asyncio tasks, all POSTing the same ``(to=ESTRENO, trigger_id)``
to the live ASGI app backed by a real PostgreSQL DB.

Asserts:
  - All responses are 200 or 202 (no 5xx).
  - Exactly one response is 202 (the first writer).
  - Exactly one ``state_transitions`` row for that ``trigger_id``.

Advisory-lock + ON CONFLICT DO NOTHING together guarantee the
"exactly one" invariant even under 50 concurrent writes.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import textwrap
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from app.main import create_app
from app.middleware.hmac_tick import TICK_SIGNATURE_HEADER
from app.scripts.bootstrap_cycle import _run as bootstrap_run
from app.settings import get_settings

API_DIR = Path(__file__).parent.parent.parent
ALEMBIC_INI = API_DIR / "alembic.ini"
_TICK_SECRET = "test-tick-secret"
_SLUG_PREFIX = "_chaos-test-"
_TRANSITION_URL = "/api/v1/internal/transition"


def _alembic_config(database_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _sign(body: bytes) -> str:
    return base64.b64encode(
        hmac.new(_TICK_SECRET.encode(), body, hashlib.sha256).digest()
    ).decode("ascii")


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
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(
            asyncio.to_thread(
                command.upgrade, _alembic_config(database_url), "head"
            )
        )
    finally:
        loop.close()


@pytest.fixture(autouse=True)
async def _cleanup(database_url: str) -> None:  # type: ignore[misc]
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        # Pre-test: wipe leftovers from any interrupted previous run.
        await conn.execute(
            sa.text(f"DELETE FROM seasons WHERE slug LIKE '{_SLUG_PREFIX}%'")
        )
    yield
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(f"DELETE FROM seasons WHERE slug LIKE '{_SLUG_PREFIX}%'")
        )
    await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _bootstrap_cycle(database_url: str, tmp_path: Path) -> None:
    slug = f"{_SLUG_PREFIX}{uuid.uuid4().hex[:8]}"
    content = textwrap.dedent(f"""\
        slug: {slug}
        title: "Chaos Test Season"
        started_on: "2026-06-10"
        bible:
          logline: "Chaos test"
        chapter:
          title: "Chaos Chapter"
          synopsis: "Test"
          manifest:
            panels: []
    """)
    p = tmp_path / f"{slug}.yaml"
    p.write_text(content, encoding="utf-8")
    await bootstrap_run(
        argparse.Namespace(
            season=slug,
            day_zero_manifest=str(p),
            force_replace=False,
        ),
        database_url=database_url,
    )


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


async def test_concurrent_ticks_exactly_one_row(
    tmp_path: Path, database_url: str
) -> None:
    """50 concurrent same-trigger_id POSTs → exactly 1 DB row, no 5xx."""
    get_settings.cache_clear()
    await _bootstrap_cycle(database_url, tmp_path)

    trigger_id = f"chaos-{uuid.uuid4()}"
    body = json.dumps(
        {"to": "ESTRENO", "ts": int(time.time()), "trigger_id": trigger_id}
    ).encode()
    sig = _sign(body)

    app = create_app()

    async def _post(client: httpx.AsyncClient) -> httpx.Response:
        return await client.post(
            _TRANSITION_URL,
            content=body,
            headers={
                "Content-Type": "application/json",
                TICK_SIGNATURE_HEADER: sig,
                "X-Dev-Skip-Dwell": "1",
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        raw: list[Any] = await asyncio.gather(*[_post(client) for _ in range(50)])
    responses: list[httpx.Response] = raw

    status_codes = [r.status_code for r in responses]
    assert all(sc in (200, 202) for sc in status_codes), (
        f"Unexpected codes: {[sc for sc in status_codes if sc not in (200, 202)]}"
    )

    applied = sum(1 for sc in status_codes if sc == 202)
    assert applied == 1, f"Expected exactly 1 applied (202), got {applied}"

    engine = create_async_engine(database_url)
    async with engine.connect() as conn:
        count = (
            await conn.execute(
                sa.text(
                    "SELECT COUNT(*) FROM state_transitions WHERE trigger_id = :tid"
                ),
                {"tid": trigger_id},
            )
        ).scalar_one()
    await engine.dispose()

    assert count == 1, f"Expected 1 state_transitions row, got {count}"
