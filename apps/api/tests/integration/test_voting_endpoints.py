"""Integration tests: GET /vote-feed + POST /vote (modules 007 / T-006 + T-007).

Covers:
  - GET happy path returns items + page + user_quota with correct shape.
  - GET cursor pagination produces non-overlapping pages whose union equals
    the full set (FR-003 + cursor round-trip).
  - GET window_closed → 409 ``window_closed``.
  - GET cursor_invalid → 422 ``cursor_invalid``.
  - POST happy path returns 200 + new_vote_count=1.
  - POST double-vote → 409 ``already_voted``.
"""
# ruff: noqa: F811 — pytest fixtures are re-imported by name for collection.

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
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
    NOW_AFTER_WINDOW,
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


def _build_vote_service(*, now: datetime = NOW_IN_WINDOW) -> VoteService:
    return VoteService(
        session_factory=get_session_factory(),
        cycle_times=CycleTimes.default(),
        max_per_chapter=5,
        allow_self_vote=True,
        now_utc=lambda: now,
    )


def _app_with_overrides(*, now: datetime = NOW_IN_WINDOW) -> Any:
    app = create_app()
    app.dependency_overrides[get_vote_service] = lambda: _build_vote_service(now=now)
    return app


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# GET /vote-feed
# ---------------------------------------------------------------------------


async def test_vote_feed_happy_returns_items_and_quota(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, chapter_id, _ = await make_active_votacion_setup(
        setup_session, "feed-happy"
    )
    user_id, code, _, token = await _make_authed_user(setup_session)
    # Create a separate author user for the twists.
    author_id_code = await _make_authed_user(setup_session)
    for i in range(3):
        await make_approved_twist(
            setup_session,
            chapter_id,
            author_id_code[0],
            f"idea numero {i} xxxx",
        )
    await setup_session.commit()
    clear_flags_cache()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            resp = await client.get(
                "/api/v1/twists/vote-feed?sort=recent",
                headers=_auth(token),
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data["items"]) == 3
        for item in data["items"]:
            assert "id" in item
            assert "content" in item
            assert item["vote_count"] == 0
            assert item["has_my_vote"] is False
        assert data["page"]["next_cursor"] is None
        assert data["page"]["limit"] == 25
        assert data["page"]["total_approved"] == 3
        assert data["user_quota"] == {"used": 0, "max": 5, "remaining": 5}
    finally:
        await cleanup(
            setup_session, season_id, (user_id, code), author_id_code[:2]
        )


async def test_vote_feed_cursor_paginates_without_overlap(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two pages of limit=2 over 5 twists cover the full set with no overlap."""
    season_id, chapter_id, _ = await make_active_votacion_setup(
        setup_session, "feed-cur"
    )
    user_id, code, _, token = await _make_authed_user(setup_session)
    author_id_code = await _make_authed_user(setup_session)
    for i in range(5):
        await make_approved_twist(
            setup_session,
            chapter_id,
            author_id_code[0],
            f"twist {i} contenido xx",
        )
    await setup_session.commit()
    clear_flags_cache()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            r1 = await client.get(
                "/api/v1/twists/vote-feed?sort=recent&limit=2",
                headers=_auth(token),
            )
            assert r1.status_code == 200
            d1 = r1.json()
            assert len(d1["items"]) == 2
            cursor = d1["page"]["next_cursor"]
            assert cursor is not None

            r2 = await client.get(
                f"/api/v1/twists/vote-feed?sort=recent&limit=2&cursor={cursor}",
                headers=_auth(token),
            )
            assert r2.status_code == 200
            d2 = r2.json()
            assert len(d2["items"]) == 2

            r3 = await client.get(
                f"/api/v1/twists/vote-feed?sort=recent&limit=2&cursor={d2['page']['next_cursor']}",
                headers=_auth(token),
            )
            assert r3.status_code == 200
            d3 = r3.json()
            assert len(d3["items"]) == 1
            assert d3["page"]["next_cursor"] is None

        ids_p1 = {it["id"] for it in d1["items"]}
        ids_p2 = {it["id"] for it in d2["items"]}
        ids_p3 = {it["id"] for it in d3["items"]}
        assert ids_p1.isdisjoint(ids_p2)
        assert ids_p1.isdisjoint(ids_p3)
        assert ids_p2.isdisjoint(ids_p3)
        assert len(ids_p1 | ids_p2 | ids_p3) == 5
    finally:
        await cleanup(
            setup_session, season_id, (user_id, code), author_id_code[:2]
        )


async def test_vote_feed_after_window_returns_409_window_closed(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, chapter_id, _ = await make_active_votacion_setup(
        setup_session, "feed-late"
    )
    user_id, code, _, token = await _make_authed_user(setup_session)
    author = await _make_authed_user(setup_session)
    await make_approved_twist(
        setup_session, chapter_id, author[0], "tardia xxxx xxx"
    )
    await setup_session.commit()
    clear_flags_cache()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides(now=NOW_AFTER_WINDOW)),
            base_url="http://test",
        ) as client:
            resp = await client.get(
                "/api/v1/twists/vote-feed",
                headers=_auth(token),
            )
        assert resp.status_code == 409
        assert resp.json()["code"] == "window_closed"
    finally:
        await cleanup(
            setup_session, season_id, (user_id, code), author[:2]
        )


async def test_vote_feed_garbage_cursor_returns_422(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _chapter_id, _ = await make_active_votacion_setup(
        setup_session, "feed-bad-cursor"
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
                "/api/v1/twists/vote-feed?cursor=!!!notbase64!!!",
                headers=_auth(token),
            )
        assert resp.status_code == 422
        assert resp.json()["code"] == "cursor_invalid"
    finally:
        await cleanup(setup_session, season_id, (user_id, code))


# ---------------------------------------------------------------------------
# POST /vote
# ---------------------------------------------------------------------------


async def test_vote_cast_endpoint_happy_returns_200(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, chapter_id, _ = await make_active_votacion_setup(
        setup_session, "cast-happy"
    )
    user_id, code, _, token = await _make_authed_user(setup_session)
    author = await _make_authed_user(setup_session)
    _, twist_public = await make_approved_twist(
        setup_session, chapter_id, author[0], "una idea aprobada xx"
    )
    await setup_session.commit()
    clear_flags_cache()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/v1/twists/vote",
                json={"twist_id": str(twist_public)},
                headers=_auth(token),
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["twist_id"] == str(twist_public)
        assert data["new_vote_count"] == 1
        assert data["user_quota"] == {"used": 1, "max": 5, "remaining": 4}
    finally:
        await cleanup(
            setup_session, season_id, (user_id, code), author[:2]
        )


async def test_vote_cast_endpoint_double_returns_409_already_voted(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, chapter_id, _ = await make_active_votacion_setup(
        setup_session, "cast-dbl"
    )
    user_id, code, _, token = await _make_authed_user(setup_session)
    author = await _make_authed_user(setup_session)
    _, twist_public = await make_approved_twist(
        setup_session, chapter_id, author[0], "repetida xxxx xx"
    )
    await setup_session.commit()
    clear_flags_cache()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            r1 = await client.post(
                "/api/v1/twists/vote",
                json={"twist_id": str(twist_public)},
                headers=_auth(token),
            )
            assert r1.status_code == 200
            r2 = await client.post(
                "/api/v1/twists/vote",
                json={"twist_id": str(twist_public)},
                headers=_auth(token),
            )
        assert r2.status_code == 409
        body = r2.json()
        assert body["code"] == "already_voted"
        assert body["twist_id"] == str(twist_public)
    finally:
        await cleanup(
            setup_session, season_id, (user_id, code), author[:2]
        )


async def test_vote_cast_unauthenticated_returns_401(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """No Authorization header → 401, no DB lookup at all."""
    season_id, chapter_id, _ = await make_active_votacion_setup(
        setup_session, "cast-noauth"
    )
    user_id, code, _, _ = await _make_authed_user(setup_session)
    _, twist_public = await make_approved_twist(
        setup_session, chapter_id, user_id, "anonimo xxx xxx"
    )
    await setup_session.commit()
    clear_flags_cache()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=_app_with_overrides()),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/api/v1/twists/vote",
                json={"twist_id": str(twist_public)},
            )
        assert resp.status_code == 401
    finally:
        await cleanup(setup_session, season_id, (user_id, code))
