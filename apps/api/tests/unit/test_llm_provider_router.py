"""Unit tests: LLMProviderRouter.

Module 006 / Task T-004.

Coverage of FR-004 fallback policy:
  - Healthy first provider succeeds → returned.
  - Unhealthy provider proactively skipped (no chat_json call).
  - Rate-limited → skip to next (no retry).
  - InvalidOutput → skip to next (no retry).
  - Unavailable → retries up to max_retries with backoff, then skip.
  - Unavailable then success → retry path returns a response.
  - Generic LLMProviderError (auth) bubbles to caller, never failover.
  - All providers exhausted → LLMProviderError with clear message.
  - Empty chain → LLMProviderError immediately.
  - chat_json kwargs (system/user/temperature/max_output_tokens) reach
    the underlying provider intact.
"""

from __future__ import annotations

from typing import cast

import pytest
from pydantic import BaseModel

from app.providers.llm import (
    FakeLLMProvider,
    LLMProvider,
    LLMProviderError,
    LLMProviderInvalidOutput,
    LLMProviderRateLimited,
    LLMProviderRouter,
    LLMProviderUnavailable,
    LLMResponse,
)


class _Verdict(BaseModel):
    accept: bool
    reason: str


_NO_SLEEP = (0.0, 0.0, 0.0)


def _ok() -> _Verdict:
    return _Verdict(accept=True, reason="ok")


# ---------------------------------------------------------------------------
# Success + health-skip
# ---------------------------------------------------------------------------


async def test_router_returns_first_provider_success() -> None:
    p1 = FakeLLMProvider(responses=[_ok()], model="m1")
    p1.name = "p1"
    p2 = FakeLLMProvider(responses=[_ok()], model="m2")
    p2.name = "p2"
    router = LLMProviderRouter(
        [p1, p2], backoff_schedule_seconds=_NO_SLEEP
    )

    resp = await router.chat_json(
        system="s", user="u", response_schema=_Verdict
    )

    assert resp.provider == "p1"
    assert resp.model == "m1"
    assert p1.remaining() == 0
    assert p2.remaining() == 1  # untouched


async def test_router_skips_unhealthy_provider_and_uses_next() -> None:
    p1 = FakeLLMProvider(responses=[_ok()], model="m1", healthy=False)
    p1.name = "p1"
    p2 = FakeLLMProvider(responses=[_ok()], model="m2")
    p2.name = "p2"
    router = LLMProviderRouter(
        [p1, p2], backoff_schedule_seconds=_NO_SLEEP
    )

    resp = await router.chat_json(
        system="s", user="u", response_schema=_Verdict
    )

    assert resp.provider == "p2"
    assert p1.remaining() == 1  # never called
    assert p2.remaining() == 0


async def test_router_check_health_false_calls_unhealthy_provider() -> None:
    """Disabling proactive health check lets the unhealthy provider try."""
    p1 = FakeLLMProvider(responses=[_ok()], model="m1", healthy=False)
    p1.name = "p1"
    router = LLMProviderRouter(
        [p1],
        backoff_schedule_seconds=_NO_SLEEP,
        check_health=False,
    )

    resp = await router.chat_json(
        system="s", user="u", response_schema=_Verdict
    )
    assert resp.provider == "p1"
    assert p1.remaining() == 0


# ---------------------------------------------------------------------------
# Failover branches: rate_limited, invalid_output (no retry)
# ---------------------------------------------------------------------------


async def test_router_falls_over_on_rate_limited_without_retry() -> None:
    p1 = FakeLLMProvider(
        responses=[LLMProviderRateLimited("quota: 0")], model="m1"
    )
    p1.name = "p1"
    p2 = FakeLLMProvider(responses=[_ok()], model="m2")
    p2.name = "p2"
    router = LLMProviderRouter(
        [p1, p2], backoff_schedule_seconds=_NO_SLEEP
    )

    resp = await router.chat_json(
        system="s", user="u", response_schema=_Verdict
    )

    assert resp.provider == "p2"
    assert p1.remaining() == 0  # single call popped the queue
    assert p2.remaining() == 0


async def test_router_falls_over_on_invalid_output_without_retry() -> None:
    p1 = FakeLLMProvider(
        responses=[LLMProviderInvalidOutput("schema mismatch")], model="m1"
    )
    p1.name = "p1"
    p2 = FakeLLMProvider(responses=[_ok()], model="m2")
    p2.name = "p2"
    router = LLMProviderRouter(
        [p1, p2], backoff_schedule_seconds=_NO_SLEEP
    )

    resp = await router.chat_json(
        system="s", user="u", response_schema=_Verdict
    )

    assert resp.provider == "p2"
    assert p1.remaining() == 0
    assert p2.remaining() == 0


# ---------------------------------------------------------------------------
# Unavailable + retry semantics
# ---------------------------------------------------------------------------


async def test_router_retries_unavailable_then_falls_over() -> None:
    """max_retries_on_unavailable=2 → total 3 attempts, then skip."""
    p1 = FakeLLMProvider(
        responses=[
            LLMProviderUnavailable("503-1"),
            LLMProviderUnavailable("503-2"),
            LLMProviderUnavailable("503-3"),
        ],
        model="m1",
    )
    p1.name = "p1"
    p2 = FakeLLMProvider(responses=[_ok()], model="m2")
    p2.name = "p2"
    router = LLMProviderRouter(
        [p1, p2],
        max_retries_on_unavailable=2,
        backoff_schedule_seconds=_NO_SLEEP,
    )

    resp = await router.chat_json(
        system="s", user="u", response_schema=_Verdict
    )

    assert resp.provider == "p2"
    assert p1.remaining() == 0  # all three popped (1 initial + 2 retries)
    assert p2.remaining() == 0


async def test_router_succeeds_after_unavailable_retry() -> None:
    """Retry path returns a response when the next attempt succeeds."""
    p1 = FakeLLMProvider(
        responses=[LLMProviderUnavailable("503"), _ok()], model="m1"
    )
    p1.name = "p1"
    p2 = FakeLLMProvider(responses=[_ok()], model="m2")
    p2.name = "p2"
    router = LLMProviderRouter(
        [p1, p2],
        max_retries_on_unavailable=2,
        backoff_schedule_seconds=_NO_SLEEP,
    )

    resp = await router.chat_json(
        system="s", user="u", response_schema=_Verdict
    )

    assert resp.provider == "p1"
    assert p1.remaining() == 0
    assert p2.remaining() == 1  # untouched


# ---------------------------------------------------------------------------
# Generic LLMProviderError bubbles (operator-actionable, no silent failover)
# ---------------------------------------------------------------------------


async def test_router_bubbles_generic_provider_error() -> None:
    p1 = FakeLLMProvider(
        responses=[LLMProviderError("auth: invalid api key")], model="m1"
    )
    p1.name = "p1"
    p2 = FakeLLMProvider(responses=[_ok()], model="m2")
    p2.name = "p2"
    router = LLMProviderRouter(
        [p1, p2], backoff_schedule_seconds=_NO_SLEEP
    )

    with pytest.raises(LLMProviderError, match="auth"):
        await router.chat_json(
            system="s", user="u", response_schema=_Verdict
        )

    # p2 must never be tried — operator must fix p1 first.
    assert p2.remaining() == 1


# ---------------------------------------------------------------------------
# Exhaustion + empty chain
# ---------------------------------------------------------------------------


async def test_router_raises_when_all_providers_exhausted() -> None:
    p1 = FakeLLMProvider(
        responses=[LLMProviderRateLimited("q")], model="m1"
    )
    p1.name = "p1"
    p2 = FakeLLMProvider(
        responses=[LLMProviderRateLimited("q")], model="m2"
    )
    p2.name = "p2"
    router = LLMProviderRouter(
        [p1, p2], backoff_schedule_seconds=_NO_SLEEP
    )

    with pytest.raises(LLMProviderError, match="all providers exhausted"):
        await router.chat_json(
            system="s", user="u", response_schema=_Verdict
        )

    assert p1.remaining() == 0
    assert p2.remaining() == 0


async def test_router_empty_chain_raises_immediately() -> None:
    router = LLMProviderRouter([], backoff_schedule_seconds=_NO_SLEEP)
    with pytest.raises(LLMProviderError, match="provider chain is empty"):
        await router.chat_json(
            system="s", user="u", response_schema=_Verdict
        )


# ---------------------------------------------------------------------------
# Plumbing: kwargs reach the underlying provider intact
# ---------------------------------------------------------------------------


class _CapturingProvider(LLMProvider):
    """Records the last chat_json kwargs to inspect from the test."""

    name = "capture"

    def __init__(self) -> None:
        self.last_kwargs: dict[str, object] | None = None
        self._payload = _ok()

    async def health(self) -> bool:
        return True

    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        response_schema: type[BaseModel],
        temperature: float = 0.2,
        max_output_tokens: int = 2048,
    ) -> LLMResponse:
        self.last_kwargs = {
            "system": system,
            "user": user,
            "response_schema": response_schema,
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
        return LLMResponse(
            content=self._payload,
            provider=self.name,
            model="cap-1",
            latency_ms=0,
            tokens_in=0,
            tokens_out=0,
        )


async def test_router_passes_through_kwargs_to_provider() -> None:
    cap = _CapturingProvider()
    router = LLMProviderRouter(
        [cap], backoff_schedule_seconds=_NO_SLEEP
    )

    await router.chat_json(
        system="SYS",
        user="USR",
        response_schema=_Verdict,
        temperature=0.7,
        max_output_tokens=512,
    )

    assert cap.last_kwargs is not None
    captured = cap.last_kwargs
    assert captured["system"] == "SYS"
    assert captured["user"] == "USR"
    assert captured["response_schema"] is _Verdict
    assert cast(float, captured["temperature"]) == pytest.approx(0.7)
    assert captured["max_output_tokens"] == 512


# ---------------------------------------------------------------------------
# Backoff clamping (defensive: max_retries > len(schedule))
# ---------------------------------------------------------------------------


async def test_router_backoff_clamps_to_last_value_when_attempts_exceed_schedule() -> None:
    """Schedule of length 1 must keep working when retries > 1."""
    p1 = FakeLLMProvider(
        responses=[
            LLMProviderUnavailable("a"),
            LLMProviderUnavailable("b"),
            LLMProviderUnavailable("c"),
        ],
        model="m1",
    )
    p1.name = "p1"
    p2 = FakeLLMProvider(responses=[_ok()], model="m2")
    p2.name = "p2"
    router = LLMProviderRouter(
        [p1, p2],
        max_retries_on_unavailable=2,
        backoff_schedule_seconds=(0.0,),  # single element
    )

    resp = await router.chat_json(
        system="s", user="u", response_schema=_Verdict
    )
    assert resp.provider == "p2"
    assert p1.remaining() == 0
