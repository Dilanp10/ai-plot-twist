"""VideoProviderRouter — fallback orchestration across video providers.

Module 012 / Task T-005.

Implements FR-005 of the video-providers spec: given an ordered chain of
providers (typically ``[HFVideoProvider, PollinationsVideoProvider]`` in
MVP), pick the first one that succeeds. The router is the only consumer
of the typed exception hierarchy declared in
:mod:`app.providers.video.base`; concrete providers raise, the router
decides what to do.

Per-provider policy (in evaluation order):

  health() → False               → skip (no generate call, no retry slot)
  :exc:`VideoProviderRateLimited`  → skip to next (no retry — quota is gone)
  :exc:`VideoProviderInvalidOutput`→ skip to next (no retry — same prompt
                                     will produce the same broken bytes)
  :exc:`VideoProviderUnavailable`  → retry up to ``max_retries_on_unavailable``
                                     times with ``backoff_schedule_seconds``
                                     waits; after budget exhausted, fall through
  :exc:`NotImplementedError`       → re-raise immediately (paid stub reached;
                                     means chain misconfiguration)
  :exc:`VideoProviderError` (base) → re-raise immediately (auth / credentials;
                                     operator must intervene)

When every provider is exhausted, the router raises
:exc:`VideoProviderError` with ``"all providers exhausted"`` so the
module 008 coordinator can branch on typed exception or the message.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import structlog

from app.providers.video.base import (
    VideoProvider,
    VideoProviderError,
    VideoProviderInvalidOutput,
    VideoProviderRateLimited,
    VideoProviderUnavailable,
    VideoRequest,
    VideoResult,
)

_log = structlog.get_logger(__name__)


class VideoProviderRouter:
    """Order-preserving fallback router for T2V providers.

    Parameters
    ----------
    providers:
        Ordered chain — the first healthy provider is tried first. An
        empty sequence is accepted at construction time; ``generate``
        then raises immediately.
    max_retries_on_unavailable:
        Number of retries (NOT counting the initial attempt) when a
        provider raises :exc:`VideoProviderUnavailable`. Default 3 →
        total of 4 attempts per provider. Must match the length of
        ``backoff_schedule_seconds`` (extra retries clamp to the last
        backoff value).
    backoff_schedule_seconds:
        Wait between attempts. ``backoff_schedule_seconds[i]`` is the
        sleep before retry ``i+1``. Defaults to ``(5.0, 15.0, 45.0)``
        as specified in the T2V NFR. Tests pass ``(0.0, 0.0, 0.0)`` to
        keep the suite fast.
    check_health:
        When ``True`` (default), ``await provider.health()`` runs before
        each ``generate`` attempt; ``False`` skips the probe.
    """

    def __init__(
        self,
        providers: Sequence[VideoProvider],
        *,
        max_retries_on_unavailable: int = 3,
        backoff_schedule_seconds: Sequence[float] = (5.0, 15.0, 45.0),
        check_health: bool = True,
    ) -> None:
        self._providers = tuple(providers)
        self._max_retries = max_retries_on_unavailable
        self._backoff = tuple(backoff_schedule_seconds)
        self._check_health = check_health

    @property
    def provider_names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self._providers)

    async def generate(self, req: VideoRequest) -> VideoResult:
        """Try each provider in order; return the first successful clip.

        Raises
        ------
        VideoProviderError
            When every provider has been exhausted (message contains
            ``"all providers exhausted"``), when the chain is empty
            (``"empty"``), or when a provider raises the base error
            (auth / credentials — propagated unchanged).
        NotImplementedError
            When a paid stub is in the chain but not configured —
            propagated immediately as a misconfiguration signal.
        """
        if not self._providers:
            raise VideoProviderError(
                "VideoProviderRouter: provider chain is empty."
            )

        last_seen: BaseException | None = None
        for provider in self._providers:
            if self._check_health and not await provider.health():
                _log.info(
                    "video_provider_health_skip",
                    provider=provider.name,
                )
                continue

            try:
                result = await self._try_provider(provider, req)
            except VideoProviderUnavailable as exc:
                last_seen = exc
                continue
            if result is not None:
                _log.info(
                    "video_provider_success",
                    provider=provider.name,
                )
                return result

        msg = "VideoProviderRouter: all providers exhausted."
        if last_seen is None:
            raise VideoProviderError(msg)
        raise VideoProviderError(msg) from last_seen

    async def _try_provider(
        self,
        provider: VideoProvider,
        req: VideoRequest,
    ) -> VideoResult | None:
        """Run one provider's full retry budget for transient failures.

        Returns the :class:`VideoResult` on success, ``None`` when this
        provider should be skipped without retries (RateLimited or
        InvalidOutput). Raises :exc:`VideoProviderUnavailable` only after
        the retry budget is exhausted so the caller can track the most
        recent failure for chained exceptions.
        """
        attempt = 0
        while True:
            try:
                return await provider.generate(req)
            except VideoProviderRateLimited:
                _log.info(
                    "video_provider_rate_limited_skip",
                    provider=provider.name,
                )
                return None
            except VideoProviderInvalidOutput:
                _log.warning(
                    "video_provider_invalid_output_skip",
                    provider=provider.name,
                )
                return None
            except VideoProviderUnavailable:
                if attempt >= self._max_retries:
                    _log.warning(
                        "video_provider_unavailable_exhausted",
                        provider=provider.name,
                        attempts=attempt + 1,
                    )
                    raise
                delay = self._backoff_for(attempt)
                _log.info(
                    "video_provider_unavailable_retry",
                    provider=provider.name,
                    attempt=attempt + 1,
                    delay_seconds=delay,
                )
                if delay > 0:
                    await asyncio.sleep(delay)
                attempt += 1
            except VideoProviderError:
                # Auth failure, malformed credentials, or anything not in
                # the routing subclasses → bubble up; failover would mask it.
                raise

    def _backoff_for(self, attempt: int) -> float:
        if not self._backoff:
            return 0.0
        if attempt >= len(self._backoff):
            return self._backoff[-1]
        return self._backoff[attempt]
