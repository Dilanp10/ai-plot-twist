"""Integration test: concurrent submits respect the per-user quota.

Module 005 / Task T-010.

Fires 10 concurrent ``POST /twists/submit`` requests for the SAME
``(user, chapter)`` using :func:`asyncio.gather`. Asserts that exactly
3 succeed (201) and the remaining 7 are clean 409 ``over_quota`` — no
5xx, no deadlock, and the DB ends with exactly 3 rows.

This validates the production race protection (FR-005 / research R-009):
``pg_advisory_xact_lock(hashtext('twist_quota:<user>:<chapter>'))``
serializes the concurrent submits, and the recount-under-lock prevents
the quota from leaking past ``MAX_TWISTS_PER_USER_PER_CHAPTER``.

A second test verifies the lock is properly scoped — distinct users
on the same chapter do NOT contend, so 4 users × 1 submit each → 4×201.
"""
# ruff: noqa: F811, RUF001, RUF002 — pytest fixtures; multiplication signs.

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient, Response
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


async def _count_user_twists(
    session: AsyncSession, user_id: int, chapter_id_subquery_sid: int
) -> int:
    result = await session.execute(
        sa.text(
            "SELECT COUNT(*) FROM twists t "
            "JOIN chapters ch ON ch.id = t.chapter_id "
            "WHERE t.user_id = :uid AND ch.season_id = :sid"
        ),
        {"uid": user_id, "sid": chapter_id_subquery_sid},
    )
    return int(result.scalar_one())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_ten_concurrent_submits_yield_three_201_seven_409(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """10 concurrent submits → exactly 3×201 + 7×409 over_quota."""
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "race-quota-001"
    )
    user_id, code, _, token = await _make_authed_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            tasks = [
                client.post(
                    "/api/v1/twists/submit",
                    json={
                        "chapter_id": str(chapter_public_id),
                        "character_id": 1,
                        "content": f"idea concurrente {i} xxxx",
                    },
                    headers={
                        **_auth_header(token),
                        "Idempotency-Key": str(uuid4()),
                    },
                )
                for i in range(10)
            ]
            responses: list[Response] = await asyncio.gather(*tasks)

        statuses = sorted([r.status_code for r in responses])
        assert statuses == [201, 201, 201, 409, 409, 409, 409, 409, 409, 409], (
            f"Expected 3×201 + 7×409 but got {statuses}\n"
            f"Bodies: {[r.json() for r in responses if r.status_code >= 400]}"
        )

        # All 409s must be over_quota (not some other failure mode).
        over_quota_count = sum(
            1
            for r in responses
            if r.status_code == 409 and r.json().get("code") == "over_quota"
        )
        assert over_quota_count == 7, (
            f"Some 409s were not over_quota: "
            f"{[r.json() for r in responses if r.status_code == 409]}"
        )

        # DB must have exactly 3 twist rows for this (user, chapter).
        assert await _count_user_twists(setup_session, user_id, season_id) == 3
    finally:
        await cleanup(setup_session, season_id, (user_id, code))


async def test_concurrent_submits_for_distinct_users_all_succeed(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """4 distinct users × 1 submit each (concurrent) → 4×201.

    Validates the advisory-lock key includes the user_id, so different
    users do NOT contend on the same chapter.
    """
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "race-iso-001"
    )
    users = [await _make_authed_user(setup_session) for _ in range(4)]
    await setup_session.commit()
    clear_flags_cache()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            tasks = [
                client.post(
                    "/api/v1/twists/submit",
                    json={
                        "chapter_id": str(chapter_public_id),
                        "character_id": 1,
                        "content": f"user {i} primera xxxx",
                    },
                    headers={
                        **_auth_header(token),
                        "Idempotency-Key": str(uuid4()),
                    },
                )
                for i, (_, _, _, token) in enumerate(users)
            ]
            responses: list[Response] = await asyncio.gather(*tasks)

        statuses = [r.status_code for r in responses]
        assert all(s == 201 for s in statuses), (
            f"Distinct users should not contend; got statuses {statuses}\n"
            f"Bodies: {[r.json() for r in responses if r.status_code >= 400]}"
        )
    finally:
        cleanup_users = [(uid, code) for uid, code, _, _ in users]
        await cleanup(setup_session, season_id, *cleanup_users)
