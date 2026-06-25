"""ImageToVideoProviderRouter — fallback orchestration across I2V providers.

Delta 008.

Mirrors ``app.providers.video.router.VideoProviderRouter`` but operates on
:class:`ImageToVideoProvider` instances and the I2V exception hierarchy.

Policy:
  health() → False           → skip (no generate, no retry slot)
  I2VRateLimited             → skip to next (no retry)
  I2VInvalidOutput           → skip to next (no retry)
  I2VUnavailable             → retry up to ``max_retries_on_unavailable``
                               times with ``backoff_schedule_seconds``;
                               after budget exhausted, fall through to next
  I2VProviderError (base)    → re-raise immediately
  NotImplementedError        → re-raise immediately (paid stub misconfigured)

When every provider is exhausted raises :exc:`I2VProviderError` with the
message ``"all I2V providers exhausted"`` so the coordinator can catch it
and fall through to Layer B (T2V).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import structlog

from .base import (
    I2VInvalidOutput,
    I2VProviderError,
    I2VRateLimited,
    I2VRequest,
    I2VResult,
    I2VUnavailable,
    ImageToVideoProvider,
)

_log = structlog.get_logger(__name__)


class ImageToVideoProviderRouter:
    """Order-preserving fallback router for I2V providers.

    Parameters
    ----------
    providers:
        Ordered chain.  The first healthy provider is tried first.
    max_retries_on_unavailable:
        Retry budget per provider on :exc:`I2VUnavailable`.
    backoff_schedule_seconds:
        Sleep schedule between retries.
    check_health:
        When ``True`` (default), ``await provider.health()`` runs before
        each ``generate`` attempt.  Pass ``False`` in tests.
    """

    def __init__(
        self,
        providers: Sequence[ImageToVideoProvider],
        *,
        max_retries_on_unavailable: int = 2,
        backoff_schedule_seconds: Sequence[float] = (5.0, 30.0),
        check_health: bool = True,
    ) -> None:
        self._providers = tuple(providers)
        self._max_retries = max_retries_on_unavailable
        self._backoff = tuple(backoff_schedule_seconds)
        self._check_health = check_health

    @property
    def provider_names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self._providers)

    async def generate(self, req: I2VRequest) -> I2VResult:
        """Try each I2V provider in order; return the first successful clip."""
        if not self._providers:
            raise I2VProviderError("ImageToVideoProviderRouter: provider chain is empty.")

        last_seen: BaseException | None = None
        for provider in self._providers:
            if self._check_health and not await provider.health():
                _log.info("i2v_provider_health_skip", provider=provider.name)
                continue

            try:
                result = await self._try_provider(provider, req)
            except I2VUnavailable as exc:
                last_seen = exc
                continue
            if result is not None:
                _log.info("i2v_provider_success", provider=provider.name)
                return result

        msg = "ImageToVideoProviderRouter: all I2V providers exhausted."
        if last_seen is None:
            raise I2VProviderError(msg)
        raise I2VProviderError(msg) from last_seen

    async def _try_provider(
        self,
        provider: ImageToVideoProvider,
        req: I2VRequest,
    ) -> I2VResult | None:
        attempt = 0
        while True:
            try:
                return await provider.generate(req)
            except I2VRateLimited:
                _log.info("i2v_provider_rate_limited_skip", provider=provider.name)
                return None
            except I2VInvalidOutput:
                _log.warning("i2v_provider_invalid_output_skip", provider=provider.name)
                return None
            except I2VUnavailable:
                if attempt >= self._max_retries:
                    _log.warning(
                        "i2v_provider_unavailable_exhausted",
                        provider=provider.name,
                        attempts=attempt + 1,
                    )
                    raise
                if attempt < len(self._backoff):
                    delay = self._backoff[attempt]
                else:
                    delay = self._backoff[-1] if self._backoff else 0.0
                _log.info(
                    "i2v_provider_unavailable_retry",
                    provider=provider.name,
                    attempt=attempt + 1,
                    delay_seconds=delay,
                )
                if delay > 0:
                    await asyncio.sleep(delay)
                attempt += 1
            except I2VProviderError:
                raise
