"""LLMProviderRouter — fallback orchestration across LLM providers.

Module 006 / Task T-004.

Implements FR-004 of the director's-filter spec: given an ordered chain
of providers (e.g. ``[GeminiProvider, GitHubModelsProvider]``), pick the
first one that succeeds. The router is the only consumer of the typed
exception hierarchy declared in :mod:`app.providers.llm.base`; concrete
providers raise, the router decides what to do.

Per-provider semantics:

  - :class:`LLMProviderRateLimited`   → skip to next (no retry).
  - :class:`LLMProviderInvalidOutput` → skip to next (no retry — bad prompt
    or model incapacity, retrying same call wastes quota).
  - :class:`LLMProviderUnavailable`   → retry up to
    ``max_retries_on_unavailable`` times with exponential backoff
    (default 1 s, 3 s per FR-004), then fall through.
  - Any other :class:`LLMProviderError` (auth, malformed credentials) →
    re-raise immediately. Operator must intervene; silently trying the
    next provider would mask a config bug.

If ``check_health=True`` (default), the router proactively calls
``await provider.health()`` before each ``chat_json`` and short-circuits
the provider if it reports unhealthy. This trades one HEAD-ish round
trip for skipping providers we already know are down.

When every provider in the chain is exhausted, the router raises
:class:`LLMProviderError` with a clear message. Module 003's
``safe_side_effect`` wrapper catches this and transitions the cycle to
``FAILED`` (spec User Story 2 ¶2).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import structlog
from pydantic import BaseModel

from app.providers.llm.base import (
    LLMProvider,
    LLMProviderError,
    LLMProviderInvalidOutput,
    LLMProviderRateLimited,
    LLMProviderUnavailable,
    LLMResponse,
)

_log = structlog.get_logger(__name__)


class LLMProviderRouter:
    """Order-preserving fallback router.

    Parameters
    ----------
    providers:
        Ordered chain — the first healthy provider is tried first. Empty
        sequences are accepted at construction time; ``chat_json`` then
        raises immediately.
    max_retries_on_unavailable:
        Number of retries (NOT counting the initial attempt) when a
        provider raises :class:`LLMProviderUnavailable`. Default 2 →
        total of 3 attempts per provider for transient failures.
    backoff_schedule_seconds:
        Wait between attempts. ``backoff_schedule_seconds[i]`` is the
        sleep before retry ``i+1``. Defaults to ``(1.0, 3.0)`` per
        FR-004. Indices past the end clamp to the last value, so a
        single-element schedule effectively becomes a constant delay.
        Tests pass ``(0.0, 0.0)`` to keep the suite fast.
    check_health:
        When ``True`` (default), ``await provider.health()`` runs before
        each ``chat_json`` attempt; a ``False`` result short-circuits the
        provider. Set to ``False`` for chains where the cost of
        ``health()`` exceeds the benefit.
    """

    def __init__(
        self,
        providers: Sequence[LLMProvider],
        *,
        max_retries_on_unavailable: int = 2,
        backoff_schedule_seconds: Sequence[float] = (1.0, 3.0),
        check_health: bool = True,
    ) -> None:
        self._providers = tuple(providers)
        self._max_retries = max_retries_on_unavailable
        self._backoff = tuple(backoff_schedule_seconds)
        self._check_health = check_health

    @property
    def provider_names(self) -> tuple[str, ...]:
        """Names of the providers in chain order — useful for logs/tests."""
        return tuple(p.name for p in self._providers)

    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        response_schema: type[BaseModel],
        temperature: float = 0.2,
        max_output_tokens: int = 2048,
    ) -> LLMResponse:
        """Try each provider in order; return the first successful response.

        Raises
        ------
        LLMProviderError
            When every provider in the chain has been exhausted, or when
            the chain is empty. Also propagates any generic
            ``LLMProviderError`` raised by a provider (auth, malformed
            credentials) — those are operator-actionable and must not be
            silently masked by failover.
        """
        if not self._providers:
            raise LLMProviderError(
                "LLMProviderRouter: provider chain is empty."
            )

        for provider in self._providers:
            if self._check_health and not await provider.health():
                _log.info(
                    "llm_provider_unhealthy_skip",
                    provider=provider.name,
                )
                continue

            response = await self._try_provider(
                provider,
                system=system,
                user=user,
                response_schema=response_schema,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )
            if response is not None:
                return response

        raise LLMProviderError(
            "LLMProviderRouter: all providers exhausted."
        )

    async def _try_provider(
        self,
        provider: LLMProvider,
        *,
        system: str,
        user: str,
        response_schema: type[BaseModel],
        temperature: float,
        max_output_tokens: int,
    ) -> LLMResponse | None:
        """Run one provider's full retry budget for transient failures.

        Returns the :class:`LLMResponse` on success, ``None`` if this
        provider should be skipped (failover). Generic
        :class:`LLMProviderError` is re-raised so the operator sees it.
        """
        attempt = 0
        while True:
            try:
                return await provider.chat_json(
                    system=system,
                    user=user,
                    response_schema=response_schema,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                )
            except LLMProviderRateLimited:
                _log.info(
                    "llm_provider_failover",
                    provider=provider.name,
                    reason="rate_limited",
                )
                return None
            except LLMProviderInvalidOutput:
                _log.warning(
                    "llm_provider_failover",
                    provider=provider.name,
                    reason="invalid_output",
                )
                return None
            except LLMProviderUnavailable:
                if attempt >= self._max_retries:
                    _log.warning(
                        "llm_provider_failover",
                        provider=provider.name,
                        reason="unavailable_after_retries",
                        attempts=attempt + 1,
                    )
                    return None
                delay = self._backoff_for(attempt)
                _log.info(
                    "llm_provider_retry",
                    provider=provider.name,
                    reason="unavailable",
                    attempt=attempt + 1,
                    delay_seconds=delay,
                )
                if delay > 0:
                    await asyncio.sleep(delay)
                attempt += 1
                continue
            except LLMProviderError:
                # Generic LLMProviderError (auth, bad credentials, ...)
                # is operator-actionable: do NOT silently failover.
                raise

    def _backoff_for(self, attempt: int) -> float:
        """Return the sleep duration before retry ``attempt+1``.

        Indices past ``len(backoff_schedule_seconds)`` clamp to the last
        value (defensive — only relevant if a caller passes
        ``max_retries_on_unavailable`` larger than the schedule length).
        """
        if not self._backoff:
            return 0.0
        if attempt >= len(self._backoff):
            return self._backoff[-1]
        return self._backoff[attempt]
