"""Integration: director's filter raises when every provider fails.

Module 006 / Task T-009. Covers User Story 2 §2.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.director_filter import run_director_filter
from app.providers.llm import (
    FakeLLMProvider,
    LLMProviderError,
    LLMProviderRateLimited,
    LLMProviderRouter,
)
from tests.integration.director_filter._helpers import (
    cleanup,
    count_pending_for_chapter,
    fetch_twist_status,
    make_season_and_chapter,
    make_user,
    seed_pending_twists,
)


async def test_all_providers_exhausted_raises_and_leaves_twists_pending(
    session: AsyncSession,
) -> None:
    season_id, chapter_id = await make_season_and_chapter(
        session, "down-001"
    )
    user = await make_user(session)
    await session.commit()

    twists = await seed_pending_twists(
        session,
        chapter_id,
        user[0],
        [
            "Una propuesta razonable número uno",
            "Otra propuesta coherente número dos",
            "Tercera idea que también encaja",
        ],
    )

    p1 = FakeLLMProvider(
        responses=[LLMProviderRateLimited("p1 down")], model="m1"
    )
    p1.name = "p1"
    p2 = FakeLLMProvider(
        responses=[LLMProviderRateLimited("p2 down")], model="m2"
    )
    p2.name = "p2"
    router = LLMProviderRouter(
        [p1, p2], backoff_schedule_seconds=(0.0, 0.0)
    )

    pre = await count_pending_for_chapter(session, chapter_id)
    assert pre == 3

    try:
        with pytest.raises(LLMProviderError, match="all providers exhausted"):
            await run_director_filter(
                chapter_id, session=session, router=router
            )

        await session.rollback()

        post = await count_pending_for_chapter(session, chapter_id)
        assert post == 3

        for t in twists:
            status, reason, reviewed_at = await fetch_twist_status(
                session, t.id
            )
            assert status == "pending_review"
            assert reason is None
            assert reviewed_at is None
    finally:
        await cleanup(session, season_id, user)
