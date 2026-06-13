"""Integration: director's filter falls over from p1 → p2.

Module 006 / Task T-009. Covers User Story 2.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.director_filter import run_director_filter
from app.domain.director_verdicts import DirectorBatchResponse
from app.providers.llm import (
    FakeLLMProvider,
    LLMProviderRateLimited,
    LLMProviderRouter,
)
from tests.integration.director_filter._helpers import (
    cleanup,
    fetch_twist_status,
    make_season_and_chapter,
    make_user,
    make_verdict,
    seed_pending_twists,
)


async def test_fallback_from_rate_limited_provider_to_next(
    session: AsyncSession,
) -> None:
    season_id, chapter_id = await make_season_and_chapter(session, "fb-001")
    user = await make_user(session)
    await session.commit()

    twists = await seed_pending_twists(
        session, chapter_id, user[0], ["Idea uno", "Idea dos", "Idea tres"]
    )

    p1 = FakeLLMProvider(
        responses=[LLMProviderRateLimited("quota: 0")], model="m1"
    )
    p1.name = "p1"

    response = DirectorBatchResponse.model_validate(
        {
            "verdicts": [
                make_verdict(twists[0].public_id, "approved", "ok-0"),
                make_verdict(twists[1].public_id, "approved", "ok-1"),
                make_verdict(
                    twists[2].public_id, "rejected_spam", "spam"
                ),
            ]
        }
    )
    p2 = FakeLLMProvider(responses=[response], model="m2")
    p2.name = "p2"

    router = LLMProviderRouter(
        [p1, p2], backoff_schedule_seconds=(0.0, 0.0)
    )

    try:
        summary = await run_director_filter(
            chapter_id, session=session, router=router
        )
        assert summary.twist_count == 3
        assert summary.batches == 1
        assert summary.approved == 2
        assert summary.rejected_spam == 1
        assert p1.remaining() == 0
        assert p2.remaining() == 0

        status, reason, reviewed_at = await fetch_twist_status(
            session, twists[2].id
        )
        assert status == "rejected_spam"
        assert reason == "spam"
        assert reviewed_at is not None
    finally:
        await cleanup(session, season_id, user)
