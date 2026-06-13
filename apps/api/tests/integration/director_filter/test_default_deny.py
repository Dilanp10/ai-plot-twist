"""Integration: director's filter default-deny on partial LLM responses.

Module 006 / Task T-009.

Covers FR-009 / User Story 3.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.director_filter import (
    DEFAULT_DENY_REASON,
    run_director_filter,
)
from app.domain.director_verdicts import DirectorBatchResponse
from app.providers.llm import FakeLLMProvider, LLMProviderRouter
from tests.integration.director_filter._helpers import (
    cleanup,
    fetch_twist_status,
    make_season_and_chapter,
    make_user,
    make_verdict,
    seed_pending_twists,
)


async def test_default_deny_fills_in_omitted_verdicts(
    session: AsyncSession,
) -> None:
    season_id, chapter_id = await make_season_and_chapter(session, "dd-001")
    user = await make_user(session)
    await session.commit()

    contents = [
        "Twist uno coherente",
        "Twist dos coherente",
        "Twist tres — LLM lo va a omitir",
        "Twist cuatro coherente",
        "Twist cinco — LLM también lo va a omitir",
    ]
    twists = await seed_pending_twists(session, chapter_id, user[0], contents)

    response = DirectorBatchResponse.model_validate(
        {
            "verdicts": [
                make_verdict(twists[0].public_id, "approved", "ok-0"),
                make_verdict(twists[1].public_id, "approved", "ok-1"),
                make_verdict(twists[3].public_id, "approved", "ok-3"),
            ]
        }
    )
    provider = FakeLLMProvider(responses=[response], model="fake-dd")
    router = LLMProviderRouter(
        [provider], backoff_schedule_seconds=(0.0,)
    )

    try:
        summary = await run_director_filter(
            chapter_id, session=session, router=router
        )
        assert summary.twist_count == 5
        assert summary.batches == 1
        assert summary.approved == 3
        assert summary.rejected_incoherent == 2
        assert summary.default_denied == 2
        assert summary.slur_overrides == 0

        for idx in (2, 4):
            status, reason, reviewed_at = await fetch_twist_status(
                session, twists[idx].id
            )
            assert status == "rejected_incoherent"
            assert reason == DEFAULT_DENY_REASON
            assert reviewed_at is not None

        status, reason, _ = await fetch_twist_status(session, twists[0].id)
        assert status == "approved"
        assert reason == "ok-0"
    finally:
        await cleanup(session, season_id, user)
