"""Fake LLM provider for tests and local development.

Module 006 / Task T-001.

Pops pre-seeded responses in order: each call to :meth:`chat_json`
consumes the next item from the ``responses`` queue. Items can be:
  - a Pydantic ``BaseModel`` instance → returned as ``LLMResponse.content``
  - an ``Exception`` instance → raised verbatim

A ``latency_ms`` knob lets tests simulate slow providers via
``asyncio.sleep``. Default 0 (no sleep) keeps the test suite fast.

Empty queue raises ``LLMProviderError("FakeLLMProvider: queue exhausted")``
so a misconfigured test fails loudly instead of silently hanging.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterable

from pydantic import BaseModel

from app.providers.llm.base import (
    LLMProvider,
    LLMProviderError,
    LLMResponse,
)


class FakeLLMProvider(LLMProvider):
    """Deterministic LLM provider for tests.

    Parameters
    ----------
    responses:
        Sequence of items to return on successive ``chat_json`` calls.
        A ``BaseModel`` instance is returned wrapped in :class:`LLMResponse`;
        an ``Exception`` instance is raised verbatim.
    latency_ms:
        Simulated end-to-end latency per call. Reported in the
        :class:`LLMResponse` and slept (when > 0) via ``asyncio.sleep``.
    model:
        Pinned model identifier. Defaults to ``"fake-1"``.
    healthy:
        Initial value of :meth:`health`. Tests can flip it at runtime
        via :attr:`set_healthy`.
    """

    name = "fake"

    def __init__(
        self,
        responses: Iterable[BaseModel | Exception],
        *,
        latency_ms: int = 0,
        model: str = "fake-1",
        healthy: bool = True,
    ) -> None:
        self._queue: deque[BaseModel | Exception] = deque(responses)
        self._latency_ms = latency_ms
        self._model = model
        self._healthy = healthy

    def set_healthy(self, value: bool) -> None:
        """Toggle :meth:`health` at runtime (e.g. simulate a provider going down)."""
        self._healthy = value

    def remaining(self) -> int:
        """Count of seeded responses still in the queue."""
        return len(self._queue)

    async def health(self) -> bool:
        return self._healthy

    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        response_schema: type[BaseModel],
        temperature: float = 0.2,
        max_output_tokens: int = 2048,
    ) -> LLMResponse:
        if self._latency_ms > 0:
            await asyncio.sleep(self._latency_ms / 1000)

        if not self._queue:
            raise LLMProviderError(
                "FakeLLMProvider: response queue exhausted; "
                "seed more entries in the test fixture."
            )

        item = self._queue.popleft()
        if isinstance(item, Exception):
            raise item
        if not isinstance(item, response_schema):
            # Catches tests that seed the wrong schema — fail loudly.
            raise LLMProviderError(
                f"FakeLLMProvider: seeded response is "
                f"{type(item).__name__!r} but caller asked for "
                f"{response_schema.__name__!r}."
            )

        return LLMResponse(
            content=item,
            provider=self.name,
            model=self._model,
            latency_ms=self._latency_ms,
            tokens_in=0,
            tokens_out=0,
        )
