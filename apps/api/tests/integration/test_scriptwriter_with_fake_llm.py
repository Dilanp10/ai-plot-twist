"""Integration tests: Scriptwriter — fake LLM backend.

Module 008 / Task T-008.

No real LLM calls — all tests use FakeLLMProvider or AsyncMock on the router.
No database required.

Coverage:
  - draft() winner mode returns ScriptwriterResponse.
  - draft() auto mode (winner_content=None) returns ScriptwriterResponse.
  - Winner mode passes the winner system prompt to the router.
  - Auto mode passes the auto-continue system prompt to the router.
  - draft() propagates LLMProviderError raised by the router.
  - draft() passes temperature=0.6 and max_output_tokens=4096.
  - draft() passes the rendered user prompt (not an empty string).
  - FakeLLMProvider queue exhaustion is surfaced as LLMProviderError.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.scriptwriter import Scriptwriter
from app.domain.scriptwriter_prompts import (
    ChapterBrief,
    ScriptContext,
    SeasonBrief,
    load_auto_system_prompt,
    load_system_prompt,
)
from app.domain.scriptwriter_response import Panel, ScriptwriterResponse
from app.providers.llm.base import LLMProviderError, LLMResponse
from app.providers.llm.fake import FakeLLMProvider
from app.providers.llm.router import LLMProviderRouter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GOOD_VISUAL = "a woman reaches into a fractured mirror, cinematic, 35mm"


def _good_script(n_panels: int = 3) -> ScriptwriterResponse:
    panels = [
        Panel(
            idx=i,
            narration="El espejo crujió como hielo viejo al amanecer.",
            visual_prompt=_GOOD_VISUAL,
            mood="tense",
            tts_text="El espejo crujió como hielo viejo al amanecer.",
        )
        for i in range(1, n_panels + 1)
    ]
    return ScriptwriterResponse(
        title="Lo que había detrás del espejo",
        synopsis="Mariana acepta la propuesta del reflejo y descubre su otra-yo.",
        panels=panels,
        cliffhanger="Entonces escuchó la voz de su madre muerta.",
        next_cliffhanger_seed="La madre del 1998 alterno está viva.",
    )


def _make_context(*, winner_content: str | None = "Propuesta ganadora.") -> ScriptContext:
    return ScriptContext(
        season=SeasonBrief(title="El espejo roto", bible_json={"genre": "thriller"}),
        recent_chapters=[],
        current_chapter=ChapterBrief(
            day_index=3,
            title="La decisión",
            synopsis="Mariana debe elegir.",
            cliffhanger="¿Qué esconde el espejo?",
        ),
        next_day_index=4,
        winner_content=winner_content,
    )


def _fake_router(script: ScriptwriterResponse) -> LLMProviderRouter:
    """Build a LLMProviderRouter backed by a single FakeLLMProvider."""
    provider = FakeLLMProvider([script])
    return LLMProviderRouter([provider], check_health=False)


def _mock_router(script: ScriptwriterResponse) -> LLMProviderRouter:
    """Build a router whose chat_json is an AsyncMock returning *script*."""
    router = MagicMock(spec=LLMProviderRouter)
    router.chat_json = AsyncMock(
        return_value=LLMResponse(
            content=script,
            provider="fake",
            model="fake-1",
            latency_ms=10,
            tokens_in=100,
            tokens_out=200,
        )
    )
    return router


# ---------------------------------------------------------------------------
# Tests — return value
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_winner_mode_returns_script() -> None:
    script = _good_script()
    sw = Scriptwriter(_fake_router(script))

    result = await sw.draft(_make_context(winner_content="La propuesta ganó."))

    assert isinstance(result, ScriptwriterResponse)
    assert result.title == script.title
    assert len(result.panels) == 3


@pytest.mark.asyncio
async def test_draft_auto_mode_returns_script() -> None:
    script = _good_script()
    sw = Scriptwriter(_fake_router(script))

    result = await sw.draft(_make_context(winner_content=None))

    assert isinstance(result, ScriptwriterResponse)
    assert result.cliffhanger == script.cliffhanger


# ---------------------------------------------------------------------------
# Tests — prompt selection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_winner_mode_uses_winner_system_prompt() -> None:
    router = _mock_router(_good_script())
    sw = Scriptwriter(router)

    await sw.draft(_make_context(winner_content="Twist: Mariana es su propia enemiga."))

    _, kwargs = router.chat_json.call_args
    assert kwargs["system"] == load_system_prompt()


@pytest.mark.asyncio
async def test_draft_auto_mode_uses_auto_system_prompt() -> None:
    router = _mock_router(_good_script())
    sw = Scriptwriter(router)

    await sw.draft(_make_context(winner_content=None))

    _, kwargs = router.chat_json.call_args
    assert kwargs["system"] == load_auto_system_prompt()


@pytest.mark.asyncio
async def test_draft_winner_and_auto_prompts_differ() -> None:
    """Sanity: the two system prompts must be distinct strings."""
    assert load_system_prompt() != load_auto_system_prompt()


# ---------------------------------------------------------------------------
# Tests — kwargs forwarded to router
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_passes_temperature_0_6() -> None:
    router = _mock_router(_good_script())
    sw = Scriptwriter(router)

    await sw.draft(_make_context())

    _, kwargs = router.chat_json.call_args
    assert kwargs["temperature"] == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_draft_passes_max_output_tokens_4096() -> None:
    router = _mock_router(_good_script())
    sw = Scriptwriter(router)

    await sw.draft(_make_context())

    _, kwargs = router.chat_json.call_args
    assert kwargs["max_output_tokens"] == 4096


@pytest.mark.asyncio
async def test_draft_passes_scriptwriter_response_schema() -> None:
    router = _mock_router(_good_script())
    sw = Scriptwriter(router)

    await sw.draft(_make_context())

    _, kwargs = router.chat_json.call_args
    assert kwargs["response_schema"] is ScriptwriterResponse


@pytest.mark.asyncio
async def test_draft_user_prompt_is_non_empty() -> None:
    router = _mock_router(_good_script())
    sw = Scriptwriter(router)

    await sw.draft(_make_context())

    _, kwargs = router.chat_json.call_args
    assert len(kwargs["user"]) > 50


# ---------------------------------------------------------------------------
# Tests — error propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_propagates_llm_provider_error() -> None:
    router = MagicMock(spec=LLMProviderRouter)
    router.chat_json = AsyncMock(
        side_effect=LLMProviderError("all providers exhausted")
    )
    sw = Scriptwriter(router)

    with pytest.raises(LLMProviderError, match="exhausted"):
        await sw.draft(_make_context())


@pytest.mark.asyncio
async def test_draft_fake_provider_queue_exhaustion_raises() -> None:
    provider = FakeLLMProvider([])  # empty queue
    router = LLMProviderRouter([provider], check_health=False)
    sw = Scriptwriter(router)

    with pytest.raises(LLMProviderError):
        await sw.draft(_make_context())
