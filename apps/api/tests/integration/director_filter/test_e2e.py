"""Integration: director's filter end-to-end happy path + edge cases.

Module 006 / Task T-009.

Covers User Story 1, FR-010 (slur post-filter override), and the
"reason > 80 chars" edge case from the spec.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.director_filter import (
    SLUR_OVERRIDE_REASON,
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


async def test_e2e_classifies_all_twists_and_handles_slur_override(
    session: AsyncSession,
) -> None:
    season_id, chapter_id = await make_season_and_chapter(session, "e2e-1")
    user = await make_user(session)
    await session.commit()

    contents = [
        "Idea coherente número uno",
        "Idea coherente número dos",
        "Otra propuesta razonable",
        "Hola pelotudo amigo",  # LLM approves, slur catches it
        "Compre criptomonedas en este link",
        "Pero los unicornios y la luna y el queso azul",
        "Sos un retrasado",  # LLM marks rejected_offensive directly
    ]
    twists = await seed_pending_twists(session, chapter_id, user[0], contents)

    response = DirectorBatchResponse.model_validate(
        {
            "verdicts": [
                make_verdict(twists[0].public_id, "approved", "coherente"),
                make_verdict(twists[1].public_id, "approved", "coherente"),
                make_verdict(twists[2].public_id, "approved", "razonable"),
                make_verdict(twists[3].public_id, "approved", "todo bien"),
                make_verdict(twists[4].public_id, "rejected_spam", "publicidad"),
                make_verdict(
                    twists[5].public_id,
                    "rejected_incoherent",
                    "no se conecta con el cliffhanger",
                ),
                make_verdict(
                    twists[6].public_id,
                    "rejected_offensive",
                    "insulto explícito",
                ),
            ]
        }
    )
    provider = FakeLLMProvider(responses=[response], model="fake-e2e")
    router = LLMProviderRouter(
        [provider], backoff_schedule_seconds=(0.0,)
    )

    try:
        summary = await run_director_filter(
            chapter_id, session=session, router=router
        )
        assert summary.twist_count == 7
        assert summary.batches == 1
        assert summary.approved == 3
        assert summary.rejected_offensive == 2  # LLM 1 + slur override 1
        assert summary.rejected_incoherent == 1
        assert summary.rejected_spam == 1
        assert summary.default_denied == 0
        assert summary.slur_overrides == 1
        assert provider.remaining() == 0

        status, reason, reviewed_at = await fetch_twist_status(
            session, twists[3].id
        )
        assert status == "rejected_offensive"
        assert reason == SLUR_OVERRIDE_REASON
        assert reviewed_at is not None

        status, reason, reviewed_at = await fetch_twist_status(
            session, twists[0].id
        )
        assert status == "approved"
        assert reason == "coherente"
        assert reviewed_at is not None
    finally:
        await cleanup(session, season_id, user)


async def test_e2e_empty_pending_batch_short_circuits_without_llm_call(
    session: AsyncSession,
) -> None:
    """No twists pending → no LLM call, empty summary, fast path."""
    season_id, chapter_id = await make_season_and_chapter(session, "e2e-empty")
    user = await make_user(session)
    await session.commit()

    provider = FakeLLMProvider(responses=[], model="fake-empty")
    router = LLMProviderRouter(
        [provider], backoff_schedule_seconds=(0.0,)
    )

    try:
        summary = await run_director_filter(
            chapter_id, session=session, router=router
        )
        assert summary.twist_count == 0
        assert summary.batches == 0
        assert summary.approved == 0
        # Empty queue would raise on call; remaining() must still be 0.
        assert provider.remaining() == 0
    finally:
        await cleanup(session, season_id, user)


async def test_e2e_reason_at_80_limit_is_persisted_verbatim(
    session: AsyncSession,
) -> None:
    """Reason exactly at the 80-char limit persists without truncation."""
    season_id, chapter_id = await make_season_and_chapter(session, "e2e-trunc")
    user = await make_user(session)
    await session.commit()

    twists = await seed_pending_twists(
        session, chapter_id, user[0], ["idea limpia para 80 chars"]
    )

    reason_80 = "x" * 80
    response = DirectorBatchResponse.model_validate(
        {
            "verdicts": [
                make_verdict(twists[0].public_id, "approved", reason_80),
            ]
        }
    )
    provider = FakeLLMProvider(responses=[response], model="fake-trunc")
    router = LLMProviderRouter(
        [provider], backoff_schedule_seconds=(0.0,)
    )

    try:
        summary = await run_director_filter(
            chapter_id, session=session, router=router
        )
        assert summary.approved == 1

        status, reason, reviewed_at = await fetch_twist_status(
            session, twists[0].id
        )
        assert status == "approved"
        assert reason is not None
        assert len(reason) == 80
        assert reviewed_at is not None
    finally:
        await cleanup(session, season_id, user)
