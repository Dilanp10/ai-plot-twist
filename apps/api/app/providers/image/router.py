"""ImageProviderRouter — fallback orchestration across image providers.

Module 009 / Task T-005.

Implements FR-005 of the image-providers spec: given an ordered chain
of providers (typically ``[Pollinations, HuggingFace]`` in MVP), pick
the first one that succeeds. The router is the only consumer of the
typed exception hierarchy declared in :mod:`app.providers.image.base`;
concrete providers raise, the router decides what to do.

Per-provider semantics:

  - :class:`ImageProviderRateLimited`   → skip to next (no retry).
  - :class:`ImageProviderInvalidOutput` → skip to next (no retry — the
    same prompt against the same model will almost certainly produce
    the same broken bytes).
  - :class:`ImageProviderUnavailable`   → retry up to
    ``max_retries_on_unavailable`` times with backoff, then fall through.
  - Any other :class:`ImageProviderError` (auth, malformed credentials)
    → re-raise immediately. Operator must intervene; silently trying the
    next provider would mask a config bug.

If ``check_health=True`` (default), the router proactively calls
``await provider.health()`` before each generate and short-circuits the
provider if it reports unhealthy. This trades one HEAD-ish round-trip
for skipping providers we already know are down.

When every provider is exhausted, the router raises
:class:`ImageProviderUnavailable` (NOT the base class) so module 008's
deadline coordinator can branch on the typed exception.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import structlog

from app.providers.image.base import (
    ImageProvider,
    ImageProviderError,
    ImageProviderInvalidOutput,
    ImageProviderRateLimited,
    ImageProviderUnavailable,
    ImageRequest,
    ImageResult,
)

_log = structlog.get_logger(__name__)


class ImageProviderRouter:
    """Order-preserving fallback router for image providers.

    Parameters
    ----------
    providers:
        Ordered chain — the first healthy provider is tried first. Empty
        sequences are accepted at construction time; ``render`` then
        raises immediately.
    max_retries_on_unavailable:
        Number of retries (NOT counting the initial attempt) when a
        provider raises :class:`ImageProviderUnavailable`. Default 2 →
        total of 3 attempts per provider for transient failures.
    backoff_schedule_seconds:
        Wait between attempts. ``backoff_schedule_seconds[i]`` is the
        sleep before retry ``i+1``. Defaults to ``(2.0, 8.0)`` — image
        generation is slower than chat completions, so longer waits
        give HF cold starts enough time to warm up. Indices past the end
        clamp to the last value. Tests pass ``(0.0, 0.0)`` to keep the
        suite fast.
    check_health:
        When ``True`` (default), ``await provider.health()`` runs before
        each ``generate`` attempt; ``False`` skips the probe.
    """

    def __init__(
        self,
        providers: Sequence[ImageProvider],
        *,
        max_retries_on_unavailable: int = 2,
        backoff_schedule_seconds: Sequence[float] = (2.0, 8.0),
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

    async def render(self, req: ImageRequest) -> ImageResult:
        """Try each provider in order; return the first successful image.

        Raises
        ------
        ImageProviderUnavailable
            When every provider has been exhausted, or when the chain is
            empty. Module 008's deadline coordinator can catch this
            specifically and treat the panel as failed.
        ImageProviderError
            Propagated unchanged when a provider raises the base error
            (auth misconfig); failing over would mask the operator-
            actionable signal.
        """
        if not self._providers:
            raise ImageProviderUnavailable(
                "ImageProviderRouter: provider chain is empty."
            )

        last_seen: BaseException | None = None
        for provider in self._providers:
            if self._check_health and not await provider.health():
                _log.info(
                    "image_provider_unhealthy_skip",
                    provider=provider.name,
                )
                continue

            try:
                result = await self._try_provider(provider, req)
            except ImageProviderUnavailable as exc:
                last_seen = exc
                continue
            if result is not None:
                return result

        msg = "ImageProviderRouter: all providers exhausted."
        if last_seen is None:
            raise ImageProviderUnavailable(msg)
        raise ImageProviderUnavailable(msg) from last_seen

    async def _try_provider(
        self,
        provider: ImageProvider,
        req: ImageRequest,
    ) -> ImageResult | None:
        """Run one provider's full retry budget for transient failures.

        Returns the :class:`ImageResult` on success, ``None`` when this
        provider should be skipped without retries (RateLimited or
        InvalidOutput). Raises :class:`ImageProviderUnavailable` only
        after the retry budget is exhausted, so the caller can keep
        track of the most recent ``Unavailable`` for the chain-exhausted
        chained exception.
        """
        attempt = 0
        while True:
            try:
                return await provider.generate(req)
            except ImageProviderRateLimited:
                _log.info(
                    "image_provider_failover",
                    provider=provider.name,
                    reason="rate_limited",
                )
                return None
            except ImageProviderInvalidOutput:
                _log.warning(
                    "image_provider_failover",
                    provider=provider.name,
                    reason="invalid_output",
                )
                return None
            except ImageProviderUnavailable:
                if attempt >= self._max_retries:
                    _log.warning(
                        "image_provider_failover",
                        provider=provider.name,
                        reason="unavailable_after_retries",
                        attempts=attempt + 1,
                    )
                    raise
                delay = self._backoff_for(attempt)
                _log.info(
                    "image_provider_retry",
                    provider=provider.name,
                    reason="unavailable",
                    attempt=attempt + 1,
                    delay_seconds=delay,
                )
                if delay > 0:
                    await asyncio.sleep(delay)
                attempt += 1
                continue
            except ImageProviderError:
                # Auth, malformed credentials, anything not in the
                # policy-routing subclasses → bubble up.
                raise

    def _backoff_for(self, attempt: int) -> float:
        if not self._backoff:
            return 0.0
        if attempt >= len(self._backoff):
            return self._backoff[-1]
        return self._backoff[attempt]
