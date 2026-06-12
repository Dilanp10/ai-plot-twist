"""Unit tests: LLMProvider base + FakeLLMProvider.

Module 006 / Task T-001.

Coverage:
  - LLMResponse dataclass is frozen.
  - LLMProvider is abstract — direct instantiation raises TypeError.
  - FakeLLMProvider returns seeded responses in FIFO order.
  - Exceptions in the seeded queue are raised verbatim.
  - Empty queue raises LLMProviderError with a helpful message.
  - Mismatched schema in queue raises LLMProviderError.
  - latency_ms is reported in the LLMResponse + slept via asyncio.
  - health() respects the constructor flag and set_healthy() runtime toggle.
"""

from __future__ import annotations

import dataclasses
import time

import pytest
from pydantic import BaseModel

from app.providers.llm import (
    FakeLLMProvider,
    LLMProvider,
    LLMProviderError,
    LLMProviderRateLimited,
    LLMResponse,
)


class _Verdict(BaseModel):
    accept: bool
    reason: str


class _OtherShape(BaseModel):
    label: str


# ---------------------------------------------------------------------------
# LLMResponse + ABC contract
# ---------------------------------------------------------------------------


def test_llm_response_is_frozen() -> None:
    resp = LLMResponse(
        content=_Verdict(accept=True, reason="ok"),
        provider="fake",
        model="fake-1",
        latency_ms=0,
        tokens_in=0,
        tokens_out=0,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        resp.provider = "gemini"  # type: ignore[misc]


def test_llm_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        LLMProvider()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# FakeLLMProvider behavior
# ---------------------------------------------------------------------------


async def test_fake_provider_returns_seeded_responses_in_order() -> None:
    a = _Verdict(accept=True, reason="ok-1")
    b = _Verdict(accept=False, reason="bad-2")
    provider = FakeLLMProvider(responses=[a, b])

    first = await provider.chat_json(
        system="s", user="u", response_schema=_Verdict
    )
    second = await provider.chat_json(
        system="s", user="u", response_schema=_Verdict
    )

    assert first.content is a
    assert second.content is b
    assert provider.remaining() == 0


async def test_fake_provider_raises_seeded_exceptions() -> None:
    err = LLMProviderRateLimited("quota: 0")
    provider = FakeLLMProvider(responses=[err])

    with pytest.raises(LLMProviderRateLimited, match="quota"):
        await provider.chat_json(
            system="s", user="u", response_schema=_Verdict
        )


async def test_fake_provider_empty_queue_raises_helpful_error() -> None:
    provider = FakeLLMProvider(responses=[])
    with pytest.raises(LLMProviderError, match="queue exhausted"):
        await provider.chat_json(
            system="s", user="u", response_schema=_Verdict
        )


async def test_fake_provider_schema_mismatch_raises() -> None:
    provider = FakeLLMProvider(responses=[_OtherShape(label="x")])
    with pytest.raises(LLMProviderError, match="_Verdict"):
        await provider.chat_json(
            system="s", user="u", response_schema=_Verdict
        )


async def test_fake_provider_response_metadata_pinned() -> None:
    provider = FakeLLMProvider(
        responses=[_Verdict(accept=True, reason="ok")],
        latency_ms=42,
        model="fake-pro",
    )
    resp = await provider.chat_json(
        system="s", user="u", response_schema=_Verdict
    )
    assert resp.provider == "fake"
    assert resp.model == "fake-pro"
    assert resp.latency_ms == 42
    assert resp.tokens_in == 0
    assert resp.tokens_out == 0


async def test_fake_provider_simulates_latency() -> None:
    provider = FakeLLMProvider(
        responses=[_Verdict(accept=True, reason="ok")],
        latency_ms=50,
    )
    t0 = time.monotonic()
    await provider.chat_json(
        system="s", user="u", response_schema=_Verdict
    )
    elapsed_ms = (time.monotonic() - t0) * 1000
    # Sleep is not exact; allow a generous margin but ensure the floor.
    assert elapsed_ms >= 40


async def test_fake_provider_health_respects_constructor_and_toggle() -> None:
    provider = FakeLLMProvider(responses=[], healthy=True)
    assert await provider.health() is True
    provider.set_healthy(False)
    assert await provider.health() is False
