"""Integration tests: concurrent vote-cast respects UNIQUE + quota.

Module 007 / Task T-008.

Two scenarios:

1. **Same-twist race** (NFR-004): 10 concurrent ``POST /vote`` for the
   same ``(user, twist)`` → exactly 1×200 + 9×409 ``already_voted``. The
   ``ON CONFLICT (twist_id, user_id) DO NOTHING`` is the idempotency
   anchor; no deadlock, no 5xx, DB ends with one ``votes`` row.

2. **Quota-edge race** (Acceptance 2.3): user already at 4/5 fires 2
   concurrent votes for DIFFERENT twists → exactly 1×200 + 1×409
   ``over_quota``. Validates the advisory lock + recount-under-lock
   pattern (the same race-protection that twist-submit uses).

Note: tasks.md asks CI to run each 50× under a dedicated runner. Locally
this file runs each test once; flake-resistance is verified by the lock
+ ON CONFLICT primitives, not by repetition (so a single green run is
informative — but the CI re-run amplifies confidence).
"""
# ruff: noqa: F811, RUF001, RUF002 — pytest fixtures; multiplication signs in docs.

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.voting import get_vote_service
from app.db import get_session_factory
from app.domain.jwt_service import JWTService
from app.domain.vote_service import VoteService
from app.domain.windows import CycleTimes
from app.infra.system_flags_repo import clear_cache as clear_flags_cache
from app.main import create_app
from app.settings import get_settings

from ._vote_service_helpers import (
    NOW_IN_WINDOW,
    _ensure_migrated,  # noqa: F401
    cleanup,
    database_url,  # noqa: F401
    fresh_invite_code,
    make_active_votacion_setup,
    make_approved_twist,
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


async def _make_authed_user(session: AsyncSession) -> tuple[int, str, UUID, str]:
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


def _build_vote_service() -> VoteService:
    return VoteService(
        session_factory=get_session_factory(),
        cycle_times=CycleTimes.default(),
        max_per_chapter=5,
        allow_self_vote=True,
        now_utc=lambda: NOW_IN_WINDOW,
    )


def _app_with_overrides() -> Any:
    app = create_app()
    app.dependency_overrides[get_vote_service] = _build_vote_service
    return app


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _count_votes_for(
    session: AsyncSession, *, twist_id: int
) -> int:
    result = await session.execute(
        sa.text("SELECT COUNT(*) FROM votes WHERE twist_id = :tid"),
        {"tid": twist_id},
    )
    return int(result.scalar_one())


async def _count_votes_for_user_chapter(
    session: AsyncSession, *, user_id: int, chapter_id: int
) -> int:
    result = await session.execute(
        sa.text(
            "SELECT COUNT(*) FROM votes "
            "WHERE user_id = :uid AND chapter_id = :cid"
        ),
        {"uid": user_id, "cid": chapter_id},
    )
    return int(result.scalar_one())


# ---------------------------------------------------------------------------
# Same-twist race
# ---------------------------------------------------------------------------


async def test_ten_concurrent_votes_same_twist_yield_one_200_nine_409(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """10 concurrent POST /vote for the same (user, twist) → 1×200 + 9×409."""
    season_id, chapter_id, _ = await make_active_votacion_setup(
        setup_session, "race-same-twist"
    )
    user_id, code, _, token = await _make_authed_user(setup_session)
    author = await _make_authed_user(setup_session)
    twist_id, twist_public = await make_approved_twist(
        setup_session, chapter_id, author[0], "una idea apretada xx"
    )
    await setup_session.commit()
    clear_flags_cache()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            tasks = [
                client.post(
                    "/api/v1/twists/vote",
                    json={"twist_id": str(twist_public)},
                    headers=_auth(token),
                )
                for _ in range(10)
            ]
            responses: list[Response] = await asyncio.gather(*tasks)

        statuses = sorted([r.status_code for r in responses])
        assert statuses == [200] + [409] * 9, (
            f"Expected 1×200 + 9×409 but got {statuses}\n"
            f"Bodies: {[r.json() for r in responses if r.status_code >= 400]}"
        )

        already_voted_count = sum(
            1
            for r in responses
            if r.status_code == 409 and r.json().get("code") == "already_voted"
        )
        assert already_voted_count == 9, (
            f"Some 409s were not already_voted: "
            f"{[r.json() for r in responses if r.status_code == 409]}"
        )

        # DB invariant: exactly one vote row for that twist.
        assert await _count_votes_for(setup_session, twist_id=twist_id) == 1
    finally:
        await cleanup(
            setup_session, season_id, (user_id, code), author[:2]
        )


# ---------------------------------------------------------------------------
# Quota-edge race
# ---------------------------------------------------------------------------


async def test_quota_edge_race_two_concurrent_votes_one_succeeds(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """User at 4/5, fires 2 concurrent votes for different twists → 1×200 + 1×409.

    Validates the advisory lock + recount-under-lock pattern: only ONE
    of the two concurrent inserts gets past the quota check (FR-006).
    """
    season_id, chapter_id, _ = await make_active_votacion_setup(
        setup_session, "race-quota-edge"
    )
    user_id, code, _, token = await _make_authed_user(setup_session)
    author = await _make_authed_user(setup_session)
    # 4 twists pre-voted, 2 fresh twists to race on
    pre_twists = [
        await make_approved_twist(
            setup_session, chapter_id, author[0], f"pre idea {i} xxxx"
        )
        for i in range(4)
    ]
    race_twists = [
        await make_approved_twist(
            setup_session, chapter_id, author[0], f"race idea {i} xxx"
        )
        for i in range(2)
    ]
    await setup_session.commit()
    clear_flags_cache()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            # Seed 4 votes serially to reach the edge.
            for _twist_id, public in pre_twists:
                r = await client.post(
                    "/api/v1/twists/vote",
                    json={"twist_id": str(public)},
                    headers=_auth(token),
                )
                assert r.status_code == 200, r.text

            # Now race 2 concurrent votes for the 2 unvoted twists.
            tasks = [
                client.post(
                    "/api/v1/twists/vote",
                    json={"twist_id": str(public)},
                    headers=_auth(token),
                )
                for _, public in race_twists
            ]
            responses: list[Response] = await asyncio.gather(*tasks)

        statuses = sorted([r.status_code for r in responses])
        assert statuses == [200, 409], (
            f"Expected 1×200 + 1×409 but got {statuses}\n"
            f"Bodies: {[r.json() for r in responses]}"
        )
        over_quota_count = sum(
            1
            for r in responses
            if r.status_code == 409 and r.json().get("code") == "over_quota"
        )
        assert over_quota_count == 1, (
            f"The 409 should be over_quota: "
            f"{[r.json() for r in responses if r.status_code == 409]}"
        )

        # DB invariant: exactly max votes for this (user, chapter).
        assert (
            await _count_votes_for_user_chapter(
                setup_session, user_id=user_id, chapter_id=chapter_id
            )
            == 5
        )
    finally:
        await cleanup(
            setup_session, season_id, (user_id, code), author[:2]
        )
