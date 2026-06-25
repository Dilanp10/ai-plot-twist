"""Integration tests: DELETE /api/v1/twists/{public_id}.

Module 005 / Task T-008.

Reuses the helpers + dependency override pattern from
``test_twists_submit_endpoint.py``. Covers happy path, idempotent
re-delete, not-found, forbidden, and no-JWT.
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
    """POST a fresh twist via the submit endpoint; return its public_id."""
    body = {"chapter_id": str(chapter_public_id), "character_id": 1, "content": content}
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


async def test_delete_endpoint_happy_returns_200(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "dep-happy-001"
    )
    user_id, code, _, token = await _make_authed_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            twist_pid = await _submit_via_endpoint(
                client, token, chapter_public_id, "Para borrar luego"
            )
            resp = await client.delete(
                f"/api/v1/twists/{twist_pid}",
                headers=_auth_header(token),
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["twist_id"] == twist_pid
        assert data["deleted_at"]
        # Quota does NOT decrement (FR-004).
        assert data["remaining_submissions"] == 2
    finally:
        await cleanup(setup_session, season_id, (user_id, code))


async def test_delete_endpoint_idempotent_replay_returns_200(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "dep-idem-001"
    )
    user_id, code, _, token = await _make_authed_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            twist_pid = await _submit_via_endpoint(
                client, token, chapter_public_id, "Doble delete OK"
            )
            first = await client.delete(
                f"/api/v1/twists/{twist_pid}",
                headers=_auth_header(token),
            )
            second = await client.delete(
                f"/api/v1/twists/{twist_pid}",
                headers=_auth_header(token),
            )
        assert first.status_code == 200
        assert second.status_code == 200
        # The replayed deleted_at must equal the original (no refresh).
        assert second.json()["deleted_at"] == first.json()["deleted_at"]
    finally:
        await cleanup(setup_session, season_id, (user_id, code))


async def test_delete_endpoint_unknown_id_returns_404(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, _ = await make_active_recepcion_setup(
        setup_session, "dep-nf-001"
    )
    user_id, code, _, token = await _make_authed_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    unknown = uuid4()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            resp = await client.delete(
                f"/api/v1/twists/{unknown}",
                headers=_auth_header(token),
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "twist_not_found"
    finally:
        await cleanup(setup_session, season_id, (user_id, code))


async def test_delete_endpoint_other_users_twist_returns_403(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "dep-forb-001"
    )
    owner_id, owner_code, _, owner_token = await _make_authed_user(setup_session)
    other_id, other_code, _, other_token = await _make_authed_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            twist_pid = await _submit_via_endpoint(
                client, owner_token, chapter_public_id, "Idea del owner"
            )
            resp = await client.delete(
                f"/api/v1/twists/{twist_pid}",
                headers=_auth_header(other_token),
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "forbidden_not_owner"
    finally:
        await cleanup(
            setup_session,
            season_id,
            (owner_id, owner_code),
            (other_id, other_code),
        )


async def test_delete_endpoint_no_jwt_returns_401(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, _ = await make_active_recepcion_setup(
        setup_session, "dep-noauth-001"
    )
    await setup_session.commit()
    clear_flags_cache()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            resp = await client.delete(f"/api/v1/twists/{uuid4()}")
        assert resp.status_code == 401
    finally:
        await cleanup(setup_session, season_id)
