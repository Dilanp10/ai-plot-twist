"""Integration tests: POST /api/v1/internal/client-log (Module 010 T-012).

Covers:
  1. Happy path — 202, no body.
  2. Validation error — missing required field returns 422.
  3. 413 when the body exceeds 4 KB.
  4. 429 when the per-IP bucket is exhausted (pre-populated via the
     shared ``db_session`` so we don't need to spam the endpoint 300+
     times).

Skips when DATABASE_URL is the conftest placeholder.

We reuse the fixture's ``db_session`` for both setup (bucket
pre-population) and cleanup. The endpoint itself uses a separate
session from ``get_session`` — we override that dependency so the
endpoint commits land on the same connection the fixture rolls back
at teardown, avoiding stray rows AND avoiding the Windows-asyncio
double-engine teardown race.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.main import create_app


def _payload() -> dict[str, str]:
    return {
        "event": "boundary",
        "message": "TypeError: foo is undefined",
        "stack": "at foo (app.js:42)",
        "route": "/today",
        "user_agent": "Mozilla/5.0 (test)",
        "app_version": "0.1.0",
        "timestamp": datetime.now(UTC).isoformat(),
    }


def _app_with_session(session: AsyncSession) -> FastAPI:
    """Build the FastAPI app with get_session overridden to the test session.

    Endpoint commits land on the fixture's connection, so its teardown
    rollback clears any rate-limit rows the endpoint inserted.
    """
    app = create_app()

    async def _override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override
    return app


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


async def test_client_log_happy_path_returns_202(
    db_session: AsyncSession,
) -> None:
    ip = f"10.0.0.{uuid4().int % 250}"
    async with AsyncClient(
        transport=ASGITransport(app=_app_with_session(db_session)),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/api/v1/internal/client-log",
            json=_payload(),
            headers={"X-Forwarded-For": ip},
        )
    assert resp.status_code == 202, resp.text
    assert resp.content == b""


# ---------------------------------------------------------------------------
# 2. Validation error — missing user_agent
# ---------------------------------------------------------------------------


async def test_client_log_missing_required_field_returns_422(
    db_session: AsyncSession,
) -> None:
    ip = f"10.0.1.{uuid4().int % 250}"
    bad = _payload()
    del bad["user_agent"]

    async with AsyncClient(
        transport=ASGITransport(app=_app_with_session(db_session)),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/api/v1/internal/client-log",
            json=bad,
            headers={"X-Forwarded-For": ip},
        )
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "invalid_payload"


# ---------------------------------------------------------------------------
# 3. 413 — payload too large
# ---------------------------------------------------------------------------


async def test_client_log_oversize_payload_returns_413(
    db_session: AsyncSession,
) -> None:
    ip = f"10.0.2.{uuid4().int % 250}"
    payload = _payload()
    # 5 KB filler pushes the body past the 4 KB cap. The cap kicks in
    # BEFORE pydantic validation, so the per-field 2 KB stack limit is
    # never reached.
    payload["stack"] = "x" * 5_000

    async with AsyncClient(
        transport=ASGITransport(app=_app_with_session(db_session)),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/api/v1/internal/client-log",
            content=json.dumps(payload),
            headers={
                "X-Forwarded-For": ip,
                "Content-Type": "application/json",
            },
        )
    assert resp.status_code == 413, resp.text
    assert resp.json()["code"] == "payload_too_large"


# ---------------------------------------------------------------------------
# 4. 429 — IP bucket exhausted
# ---------------------------------------------------------------------------


async def test_client_log_rate_limited_returns_429(
    db_session: AsyncSession,
) -> None:
    ip = f"10.0.3.{uuid4().int % 250}"
    bucket = f"client_log:ip:{ip}"

    # Pre-populate the bucket at its max so the next call trips it.
    await db_session.execute(
        sa.text(
            "INSERT INTO rate_limit_buckets "
            "(bucket_key, window_start, count) "
            "VALUES (:k, date_trunc('hour', now()), :c)"
        ),
        {"k": bucket, "c": 300},
    )
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=_app_with_session(db_session)),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/api/v1/internal/client-log",
            json=_payload(),
            headers={"X-Forwarded-For": ip},
        )
    assert resp.status_code == 429, resp.text
    assert resp.json()["code"] == "rate_limited"
    assert int(resp.headers["Retry-After"]) >= 1
