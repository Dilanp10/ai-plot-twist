"""Integration tests: POST /api/v1/internal/director/replay (T-011).

5 scenarios:
  1. Happy path: 3 twists in mixed pre-existing statuses re-classified;
     breakdown matches the LLM response.
  2. ``deleted_by_user`` rows are skipped by the orchestrator even
     though the verdict references them.
  3. Auth: missing Authorization → 401 missing_admin_token; bad token
     → 403 bad_admin_token.
  4. Unknown chapter_id → 404 chapter_not_found.
  5. ``app.state.director_router`` unset → 503
     director_router_unavailable.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.director_verdicts import DirectorBatchResponse
from app.providers.llm import FakeLLMProvider, LLMProviderRouter
from tests.integration.director_replay._helpers import (
    cleanup,
    fetch_twist_status,
    force_status,
    make_season_and_chapter,
    make_user,
    make_verdict,
    seed_pending_twists,
)


async def _async_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    )


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


async def test_replay_happy_path_returns_breakdown_and_persists(
    session: AsyncSession,
    app_factory: Callable[..., FastAPI],
    admin_token: str,
) -> None:
    season_id, chapter_id, chapter_public_id = await make_season_and_chapter(
        session, "happy"
    )
    user = await make_user(session)
    await session.commit()

    contents = [
        "Primera idea coherente con la trama",
        "Segunda idea que el LLM va a rechazar",
        "Tercera propuesta razonable y limpia",
    ]
    twists = await seed_pending_twists(
        session, chapter_id, user[0], contents
    )

    # Pre-classify twist[0] to validate that the relaxed guard kicks in.
    await force_status(session, twists[0].id, "approved", "old reason")

    response = DirectorBatchResponse.model_validate(
        {
            "verdicts": [
                make_verdict(
                    twists[0].public_id,
                    "rejected_incoherent",
                    "ya no encaja",
                ),
                make_verdict(
                    twists[1].public_id,
                    "rejected_spam",
                    "publicidad",
                ),
                make_verdict(
                    twists[2].public_id, "approved", "ok"
                ),
            ]
        }
    )
    provider = FakeLLMProvider(responses=[response], model="fake-dr")
    router = LLMProviderRouter(
        [provider], backoff_schedule_seconds=(0.0,)
    )

    app = app_factory(director_router=router)

    try:
        client = await _async_client(app)
        async with client:
            resp = await client.post(
                "/api/v1/internal/director/replay",
                json={"chapter_id": str(chapter_public_id)},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["chapter_id"] == str(chapter_public_id)
        assert body["twist_count"] == 3
        assert body["batches"] == 1
        assert body["classified"] == 3
        assert body["breakdown"] == {
            "approved": 1,
            "rejected_offensive": 0,
            "rejected_incoherent": 1,
            "rejected_spam": 1,
        }
        assert body["default_denied"] == 0
        assert body["slur_overrides"] == 0

        # Persistence: twist[0] was overwritten despite being 'approved'.
        status0, reason0, reviewed0 = await fetch_twist_status(
            session, twists[0].id
        )
        assert status0 == "rejected_incoherent"
        assert reason0 == "ya no encaja"
        assert reviewed0 is not None
    finally:
        await cleanup(session, season_id, user)


# ---------------------------------------------------------------------------
# 2. deleted_by_user is sacred
# ---------------------------------------------------------------------------


async def test_replay_skips_deleted_by_user_even_when_verdict_references_it(
    session: AsyncSession,
    app_factory: Callable[..., FastAPI],
    admin_token: str,
) -> None:
    season_id, chapter_id, chapter_public_id = await make_season_and_chapter(
        session, "deleted"
    )
    user = await make_user(session)
    await session.commit()

    twists = await seed_pending_twists(
        session,
        chapter_id,
        user[0],
        ["Idea que sigue pending", "Idea que el usuario borra"],
    )
    # Hard-soft-delete twist[1] before the replay runs.
    from app.infra.twists_repo import TwistsRepo

    repo = TwistsRepo(session)
    await repo.soft_delete(twists[1].id)
    await session.commit()

    # The LLM's verdict for the deleted twist must be ignored.
    response = DirectorBatchResponse.model_validate(
        {
            "verdicts": [
                make_verdict(twists[0].public_id, "approved", "ok"),
                make_verdict(
                    twists[1].public_id,
                    "approved",
                    "should never persist",
                ),
            ]
        }
    )
    provider = FakeLLMProvider(responses=[response], model="fake-dr")
    router = LLMProviderRouter(
        [provider], backoff_schedule_seconds=(0.0,)
    )
    app = app_factory(director_router=router)

    try:
        client = await _async_client(app)
        async with client:
            resp = await client.post(
                "/api/v1/internal/director/replay",
                json={"chapter_id": str(chapter_public_id)},
                headers={"Authorization": f"Bearer {admin_token}"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # The orchestrator's list_all_for_chapter_for_replay excludes
        # deleted_by_user, so twist_count = 1.
        assert body["twist_count"] == 1
        assert body["breakdown"]["approved"] == 1

        # deleted_by_user is untouched.
        status_del, reason_del, _ = await fetch_twist_status(
            session, twists[1].id
        )
        assert status_del == "deleted_by_user"
        assert reason_del is None
    finally:
        await cleanup(session, season_id, user)


# ---------------------------------------------------------------------------
# 3. Auth: missing / bad token
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("headers", "expected_status", "expected_code"),
    [
        ({}, 401, "missing_admin_token"),
        (
            {"Authorization": "Bearer wrong-token"},
            403,
            "bad_admin_token",
        ),
    ],
)
async def test_replay_admin_token_rejects(
    session: AsyncSession,
    app_factory: Callable[..., FastAPI],
    admin_token: str,
    headers: dict[str, str],
    expected_status: int,
    expected_code: str,
) -> None:
    season_id, chapter_id, chapter_public_id = await make_season_and_chapter(
        session, "auth"
    )
    user = await make_user(session)
    await session.commit()

    # Router must be set so the 503 branch is not what gets hit.
    response = DirectorBatchResponse.model_validate({"verdicts": []})
    provider = FakeLLMProvider(responses=[response], model="fake-dr")
    router = LLMProviderRouter(
        [provider], backoff_schedule_seconds=(0.0,)
    )
    app = app_factory(director_router=router)

    try:
        client = await _async_client(app)
        async with client:
            resp = await client.post(
                "/api/v1/internal/director/replay",
                json={"chapter_id": str(chapter_public_id)},
                headers=headers,
            )
        assert resp.status_code == expected_status, resp.text
        body = resp.json()
        assert body["code"] == expected_code
    finally:
        await cleanup(session, season_id, user)
    # ensure chapter id parameter referenced
    _ = chapter_id


# ---------------------------------------------------------------------------
# 4. Unknown chapter
# ---------------------------------------------------------------------------


async def test_replay_unknown_chapter_returns_404(
    session: AsyncSession,
    app_factory: Callable[..., FastAPI],
    admin_token: str,
) -> None:
    response = DirectorBatchResponse.model_validate({"verdicts": []})
    provider = FakeLLMProvider(responses=[response], model="fake-dr")
    router = LLMProviderRouter(
        [provider], backoff_schedule_seconds=(0.0,)
    )
    app = app_factory(director_router=router)

    bogus_uuid = uuid4()
    client = await _async_client(app)
    async with client:
        resp = await client.post(
            "/api/v1/internal/director/replay",
            json={"chapter_id": str(bogus_uuid)},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["code"] == "chapter_not_found"


# ---------------------------------------------------------------------------
# 5. Router not configured
# ---------------------------------------------------------------------------


async def test_replay_503_when_director_router_unavailable(
    session: AsyncSession,
    app_factory: Callable[..., FastAPI],
    admin_token: str,
) -> None:
    season_id, chapter_id, chapter_public_id = await make_season_and_chapter(
        session, "no-router"
    )
    user = await make_user(session)
    await session.commit()

    # Deliberately omit director_router → app.state has no attribute set.
    app = app_factory()

    try:
        client = await _async_client(app)
        async with client:
            resp = await client.post(
                "/api/v1/internal/director/replay",
                json={"chapter_id": str(chapter_public_id)},
                headers={"Authorization": f"Bearer {admin_token}"},
            )
        assert resp.status_code == 503, resp.text
        body = resp.json()
        assert body["code"] == "director_router_unavailable"
    finally:
        await cleanup(session, season_id, user)
    _ = chapter_id
