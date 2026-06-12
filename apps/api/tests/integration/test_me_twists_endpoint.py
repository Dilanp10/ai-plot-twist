"""Integration tests: GET /api/v1/me/twists.

Module 005 / Task T-009.

Covers: happy with mixed statuses, empty, kill_switch 503, no JWT 401,
and no-live-chapter (returns empty + quota.used=0).
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
    fresh_idempotency_key,
    fresh_invite_code,
    make_active_recepcion_setup,
    session_factory,  # noqa: F401
    setup_session,  # noqa: F401
)


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


async def _submit_via_endpoint(
    client: AsyncClient,
    token: str,
    chapter_public_id: UUID,
    content: str,
) -> str:
    body = {"chapter_id": str(chapter_public_id), "content": content}
    resp = await client.post(
        "/api/v1/twists/submit",
        json=body,
        headers={
            **_auth_header(token),
            "Idempotency-Key": fresh_idempotency_key(),
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["twist"]["public_id"])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_me_twists_endpoint_returns_mixed_statuses(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two pending + 1 deleted are all returned; quota=3/3 (delete not free)."""
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "me-ep-mix-001"
    )
    user_id, code, _, token = await _make_authed_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            a = await _submit_via_endpoint(
                client, token, chapter_public_id, "idea uno xxxx"
            )
            await _submit_via_endpoint(
                client, token, chapter_public_id, "idea dos xxxx"
            )
            await _submit_via_endpoint(
                client, token, chapter_public_id, "idea tres xxxx"
            )
            # Soft-delete the first via the DELETE endpoint.
            await client.delete(
                f"/api/v1/twists/{a}", headers=_auth_header(token)
            )

            resp = await client.get(
                "/api/v1/me/twists", headers=_auth_header(token)
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 3
        assert data["quota"] == {"used": 3, "max": 3, "remaining": 0}
        statuses = {item["status"] for item in data["items"]}
        assert statuses == {"deleted_by_user", "pending_review"}
    finally:
        await cleanup(setup_session, season_id, (user_id, code))


async def test_me_twists_endpoint_empty_for_user_without_twists(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, _ = await make_active_recepcion_setup(
        setup_session, "me-ep-empty-001"
    )
    user_id, code, _, token = await _make_authed_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            resp = await client.get(
                "/api/v1/me/twists", headers=_auth_header(token)
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["quota"] == {"used": 0, "max": 3, "remaining": 3}
    finally:
        await cleanup(setup_session, season_id, (user_id, code))


async def test_me_twists_endpoint_no_jwt_returns_401(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, _ = await make_active_recepcion_setup(
        setup_session, "me-ep-noauth-001"
    )
    await setup_session.commit()
    clear_flags_cache()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            resp = await client.get("/api/v1/me/twists")
        assert resp.status_code == 401
    finally:
        await cleanup(setup_session, season_id)


async def test_me_twists_endpoint_kill_switch_returns_503(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, _ = await make_active_recepcion_setup(
        setup_session, "me-ep-kill-001"
    )
    user_id, code, _, token = await _make_authed_user(setup_session)
    await setup_session.execute(
        sa.text(
            "UPDATE system_flags SET flag_value = "
            "cast('{\"on\": true, \"reason\": \"me test\"}' AS jsonb), "
            "updated_by = 'test', updated_at = now() "
            "WHERE flag_key = 'kill_switch'"
        )
    )
    await setup_session.commit()
    clear_flags_cache()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            resp = await client.get(
                "/api/v1/me/twists", headers=_auth_header(token)
            )
        assert resp.status_code == 503
        data = resp.json()
        assert data["code"] == "under_maintenance"
        assert data["reason"] == "me test"
    finally:
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


async def test_me_twists_endpoint_empty_when_no_live_chapter(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """No live chapter → 200 + empty items + quota=0/max (defensive, no error)."""
    season_id, _, _ = await make_active_recepcion_setup(
        setup_session, "me-ep-nochap-001"
    )
    user_id, code, _, token = await _make_authed_user(setup_session)
    # Demote the chapter from 'live' to 'ready'.
    await setup_session.execute(
        sa.text(
            "UPDATE chapters SET status = 'ready' WHERE season_id = :sid"
        ),
        {"sid": season_id},
    )
    await setup_session.commit()
    clear_flags_cache()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            resp = await client.get(
                "/api/v1/me/twists", headers=_auth_header(token)
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["quota"] == {"used": 0, "max": 3, "remaining": 3}
    finally:
        await cleanup(setup_session, season_id, (user_id, code))
