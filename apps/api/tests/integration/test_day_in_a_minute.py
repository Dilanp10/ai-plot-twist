"""Integration test: day-in-a-minute e2e (T-025).

Bootstraps a real DB cycle, then force-fires all 7 FSM transitions in
sequence using ``X-Dev-Skip-Dwell: 1`` to bypass dwell gates:

  PENDING_RELEASE → ESTRENO → RECEPCION_IDEAS → FILTERING
  → VOTACION → GENERACION → PENDING_RELEASE → ESTRENO (day 2)

Side effects use the no-op module-003 stubs (director_filter,
generation_pipeline).  The test asserts:
  - Each HTTP response is 202 with the expected ``side_effect_spawned``.
  - Exactly 7 ``state_transitions`` rows exist in the correct order.
  - The cycle ends in ESTRENO.
  - No transition to FAILED occurs.

Note: ``chapter.day_index`` stays at 1 with stubs; increment requires the
real generation_pipeline (module 008).
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
_SLUG_PREFIX = "_dim-test-"
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


async def _bootstrap_cycle(database_url: str, tmp_path: Path) -> int:
    """Create season + chapter + cycle in PENDING_RELEASE; return cycle_id."""
    slug = f"{_SLUG_PREFIX}{uuid.uuid4().hex[:8]}"
    content = textwrap.dedent(f"""\
        slug: {slug}
        title: "Day-in-a-Minute Season"
        started_on: "2026-06-10"
        bible:
          logline: "E2E test"
        chapter:
          title: "Capítulo 1"
          synopsis: "Test synopsis"
          manifest:
            panels: []
    """)
    p = tmp_path / f"{slug}.yaml"
    p.write_text(content, encoding="utf-8")
    result = await bootstrap_run(
        argparse.Namespace(
            season=slug,
            day_zero_manifest=str(p),
            force_replace=False,
        ),
        database_url=database_url,
    )
    return result.cycle_id


async def _tick(client: httpx.AsyncClient, to: str) -> httpx.Response:
    body = json.dumps(
        {"to": to, "ts": int(time.time()), "trigger_id": str(uuid.uuid4())}
    ).encode()
    return await client.post(
        _TRANSITION_URL,
        content=body,
        headers={
            "Content-Type": "application/json",
            TICK_SIGNATURE_HEADER: _sign(body),
            "X-Dev-Skip-Dwell": "1",
        },
    )


# ---------------------------------------------------------------------------
# Expected sequences
# ---------------------------------------------------------------------------

# Each entry: (to_state, expected side_effect_spawned or None)
_STEPS: list[tuple[str, str | None]] = [
    ("ESTRENO", None),
    ("RECEPCION_IDEAS", None),
    ("FILTERING", "director_filter"),
    ("VOTACION", None),
    ("GENERACION", "generation_pipeline"),
    ("PENDING_RELEASE", None),
    ("ESTRENO", None),  # day 2
]

_EXPECTED_TRANSITIONS: list[tuple[str, str]] = [
    ("PENDING_RELEASE", "ESTRENO"),
    ("ESTRENO", "RECEPCION_IDEAS"),
    ("RECEPCION_IDEAS", "FILTERING"),
    ("FILTERING", "VOTACION"),
    ("VOTACION", "GENERACION"),
    ("GENERACION", "PENDING_RELEASE"),
    ("PENDING_RELEASE", "ESTRENO"),
]


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


async def test_day_in_a_minute_full_cycle(
    tmp_path: Path, database_url: str
) -> None:
    """Full FSM loop via 7 skip-dwell ticks — no FAILED, all transitions recorded."""
    get_settings.cache_clear()
    cycle_id = await _bootstrap_cycle(database_url, tmp_path)

    app = create_app()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        for to, expected_side_effect in _STEPS:
            r = await _tick(client, to)
            assert r.status_code == 202, (
                f"POST to={to!r} returned {r.status_code}: {r.text}"
            )
            data: dict[str, Any] = r.json()
            assert data["status"] == "applied", (
                f"to={to!r}: unexpected status {data.get('status')!r}"
            )
            assert data["side_effect_spawned"] == expected_side_effect, (
                f"to={to!r}: expected side_effect_spawned={expected_side_effect!r}, "
                f"got {data['side_effect_spawned']!r}"
            )

    # Verify DB state after the full loop.
    engine = create_async_engine(database_url)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                sa.text(
                    "SELECT from_state, to_state FROM state_transitions "
                    "WHERE cycle_id = :cid ORDER BY created_at ASC, id ASC"
                ),
                {"cid": cycle_id},
            )
        ).fetchall()

        cycle_state = (
            await conn.execute(
                sa.text("SELECT state FROM cycles WHERE id = :cid"),
                {"cid": cycle_id},
            )
        ).scalar_one()
    await engine.dispose()

    assert len(rows) == 7, (
        f"Expected 7 state_transitions, got {len(rows)}: {rows!r}"
    )

    for i, ((exp_from, exp_to), row) in enumerate(
        zip(_EXPECTED_TRANSITIONS, rows, strict=True)
    ):
        got_from, got_to = row[0], row[1]
        assert (got_from, got_to) == (exp_from, exp_to), (
            f"Transition {i}: expected {exp_from}→{exp_to}, "
            f"got {got_from}→{got_to}"
        )

    assert all(row[1] != "FAILED" for row in rows), (
        f"Unexpected FAILED transition: {rows!r}"
    )
    assert cycle_state == "ESTRENO", (
        f"Expected cycle in ESTRENO, got {cycle_state!r}"
    )
