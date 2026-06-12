"""Integration tests: POST /api/v1/twists/submit.

Module 005 / Task T-007.

Skips when DATABASE_URL is the conftest placeholder. Uses httpx
``AsyncClient`` over ``ASGITransport(app=create_app())`` and a
FastAPI ``dependency_overrides`` to inject a service with a fixed
``now_utc`` so the submit window is deterministic regardless of the
wall clock at test-run time.

Coverage:
  - 201 happy path; body has the expected shape.
  - 200 idempotent replay (same Idempotency-Key + same body).
  - 422 missing/invalid Idempotency-Key header.
  - 401 no JWT.
  - 409 over_quota after 3 successful submits.
  - 503 under_maintenance when kill_switch.on=true.
"""
# ruff: noqa: F811 — pytest fixtures are re-imported by name for collection.

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.twists import get_twist_submission_service
from app.db import get_session_factory
from app.domain.jwt_service import JWTService
from app.domain.twist_submission import TwistSubmissionService
from app.domain.windows import CycleTimes
from app.infra.system_flags_repo import clear_cache as clear_flags_cache
from app.main import create_app
from app.settings import get_settings

from ._twist_submit_helpers import (
    NOW_IN_WINDOW,
    _ensure_migrated,  # noqa: F401
    cleanup,
    database_url,  # noqa: F401
    fresh_invite_code,
    make_active_recepcion_setup,
    session_factory,  # noqa: F401
    setup_session,  # noqa: F401
)

# ---------------------------------------------------------------------------
# Per-test engine reset
# ---------------------------------------------------------------------------
#
# ``app.db`` keeps a process-wide singleton engine. Each pytest-asyncio test
# runs in its own event loop; reusing the engine across tests reuses asyncpg
# connections bound to the previous (closed) loop, which raises "Event loop
# is closed". Disposing before and after each test makes each test create a
# fresh engine bound to its own loop.


@pytest.fixture(autouse=True)
async def _reset_db_engine() -> AsyncIterator[None]:
    from app.db import dispose_engine

    await dispose_engine()
    yield
    await dispose_engine()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_authed_user(
    session: AsyncSession,
) -> tuple[int, str, UUID, str]:
    """Insert invite + user, sign a JWT. Return (user_id, code, public_id, token)."""
    from datetime import UTC, datetime, timedelta

    code = fresh_invite_code()
    expires_at = datetime.now(UTC) + timedelta(days=7)
    await session.execute(
        sa.text(
            "INSERT INTO invites (code, issued_by, expires_at, status) "
            "VALUES (:code, 'test', :expires_at, 'unused')"
        ),
        {"code": code, "expires_at": expires_at},
    )
    result = await session.execute(
        sa.text(
            "INSERT INTO users (display_name, invite_code, device_token) "
            "VALUES ('TmpUser', :code, :token) "
            "RETURNING id, public_id"
        ),
        {"code": code, "token": (uuid4().hex * 2)[:64]},
    )
    row = result.mappings().one()
    user_id = int(row["id"])
    public_id = UUID(str(row["public_id"]))

    settings = get_settings()
    token, _ = JWTService(settings.jwt_secret).issue(public_id)
    return user_id, code, public_id, token


def _override_service_with_fixed_clock() -> TwistSubmissionService:
    """Build a service with ``now_utc`` pinned to ``NOW_IN_WINDOW`` for tests."""
    return TwistSubmissionService(
        session_factory=get_session_factory(),
        cycle_times=CycleTimes.default(),
        max_per_chapter=3,
        now_utc=lambda: NOW_IN_WINDOW,
    )


def _app_with_overrides() -> Any:
    app = create_app()
    app.dependency_overrides[get_twist_submission_service] = (
        _override_service_with_fixed_clock
    )
    return app


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_submit_endpoint_happy_path_returns_201(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "ep-happy-001"
    )
    user_id, code, _, token = await _make_authed_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    body = {
        "chapter_id": str(chapter_public_id),
        "content": "Una idea brillante",
    }
    headers = {**_auth_header(token), "Idempotency-Key": str(uuid4())}

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/v1/twists/submit", json=body, headers=headers
            )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["twist"]["content"] == "Una idea brillante"
        assert data["twist"]["status"] == "pending_review"
        assert UUID(data["twist"]["public_id"])
        assert data["remaining_submissions"] == 2
    finally:
        await cleanup(setup_session, season_id, (user_id, code))


async def test_submit_endpoint_idempotent_replay_returns_200(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "ep-idem-001"
    )
    user_id, code, _, token = await _make_authed_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    body = {
        "chapter_id": str(chapter_public_id),
        "content": "Para repetir igual",
    }
    idem_key = str(uuid4())
    headers = {**_auth_header(token), "Idempotency-Key": idem_key}

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            first = await client.post(
                "/api/v1/twists/submit", json=body, headers=headers
            )
            second = await client.post(
                "/api/v1/twists/submit", json=body, headers=headers
            )
        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json()["twist"]["public_id"] == first.json()["twist"]["public_id"]
        assert second.json()["remaining_submissions"] == 2
    finally:
        await cleanup(setup_session, season_id, (user_id, code))


async def test_submit_endpoint_missing_idempotency_key_returns_422(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "ep-noidem-001"
    )
    user_id, code, _, token = await _make_authed_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    body = {"chapter_id": str(chapter_public_id), "content": "Sin idem-key"}

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            # No Idempotency-Key header.
            resp = await client.post(
                "/api/v1/twists/submit", json=body, headers=_auth_header(token)
            )
        assert resp.status_code == 422
        assert resp.json()["code"] == "missing_idempotency_key"
    finally:
        await cleanup(setup_session, season_id, (user_id, code))


async def test_submit_endpoint_no_jwt_returns_401(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """No Authorization header → 401 from the JWT middleware (before our handler)."""
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "ep-noauth-001"
    )
    await setup_session.commit()
    clear_flags_cache()

    body = {"chapter_id": str(chapter_public_id), "content": "Sin token alguno"}
    headers = {"Idempotency-Key": str(uuid4())}

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/v1/twists/submit", json=body, headers=headers
            )
        assert resp.status_code == 401
    finally:
        await cleanup(setup_session, season_id)


async def test_submit_endpoint_over_quota_returns_409(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "ep-over-001"
    )
    user_id, code, _, token = await _make_authed_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            for i in range(3):
                resp = await client.post(
                    "/api/v1/twists/submit",
                    json={
                        "chapter_id": str(chapter_public_id),
                        "content": f"idea {i} xxxxx",
                    },
                    headers={
                        **_auth_header(token),
                        "Idempotency-Key": str(uuid4()),
                    },
                )
                assert resp.status_code == 201
            # 4th submit overflows.
            resp = await client.post(
                "/api/v1/twists/submit",
                json={
                    "chapter_id": str(chapter_public_id),
                    "content": "cuarta deberia fallar",
                },
                headers={
                    **_auth_header(token),
                    "Idempotency-Key": str(uuid4()),
                },
            )
        assert resp.status_code == 409
        body = resp.json()
        assert body["code"] == "over_quota"
        assert body["quota_used"] == 3
        assert body["quota_max"] == 3
    finally:
        await cleanup(setup_session, season_id, (user_id, code))


async def test_submit_endpoint_kill_switch_returns_503(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "ep-kill-001"
    )
    user_id, code, _, token = await _make_authed_user(setup_session)
    # Kill switch on.
    await setup_session.execute(
        sa.text(
            "UPDATE system_flags SET flag_value = "
            "cast('{\"on\": true, \"reason\": \"endpoint test\"}' AS jsonb), "
            "updated_by = 'test', updated_at = now() "
            "WHERE flag_key = 'kill_switch'"
        )
    )
    await setup_session.commit()
    clear_flags_cache()

    body = {"chapter_id": str(chapter_public_id), "content": "Bajo maintenance"}
    headers = {**_auth_header(token), "Idempotency-Key": str(uuid4())}

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/v1/twists/submit", json=body, headers=headers
            )
        assert resp.status_code == 503
        data = resp.json()
        assert data["code"] == "under_maintenance"
        assert data["reason"] == "endpoint test"
        assert resp.headers.get("retry-after") == "3600"
    finally:
        # Reset kill switch.
        await setup_session.execute(
            sa.text(
                "UPDATE system_flags SET flag_value = "
                "cast('{\"on\": false, \"reason\": null}' AS jsonb), "
                "updated_by = 'test', updated_at = now() "
                "WHERE flag_key = 'kill_switch'"
            )
        )
        await setup_session.commit()
        clear_flags_cache()
        await cleanup(setup_session, season_id, (user_id, code))
