"""PollinationsVideoProvider — text-to-video via video.pollinations.ai.

Module 012 / Task T-004.

Pollinations video beta is a free, no-auth HTTP service that returns a
generated MP4 clip directly from a GET URL:

  GET https://video.pollinations.ai/prompt/{encoded_prompt}
      ?seed=S
      [&model=M]
      [&width=W&height=H]

The provider reuses :func:`~app.providers.video.hf._parse_mp4_duration`
(struct-based mvhd walking) for the 80% duration threshold check.

Exception mapping (router policy lives in :class:`VideoProviderRouter`):

  HTTP 429                             → ``VideoProviderRateLimited``
  HTTP 402 (x402 "Queue full for IP") → ``VideoProviderRateLimited``
  HTTP 5xx / timeout / connect error  → ``VideoProviderUnavailable``
  Empty body / corrupt MP4 / short    → ``VideoProviderInvalidOutput``
  Other 4xx                           → ``VideoProviderError``
"""

from __future__ import annotations

import time
import urllib.parse
from typing import Any

import httpx

from app.providers.video.base import (
    VideoProvider,
    VideoProviderError,
    VideoProviderInvalidOutput,
    VideoProviderRateLimited,
    VideoProviderUnavailable,
    VideoRequest,
    VideoResult,
)
from app.providers.video.hf import _parse_mp4_duration

_BASE_URL = "https://video.pollinations.ai"
_HEALTH_PATH = "/"
_PROMPT_PATH = "/prompt"
_DEFAULT_MODEL = "wan2.1"
_HEALTH_TIMEOUT_S = 2.0
_DEFAULT_GENERATE_TIMEOUT_S = 120.0


class PollinationsVideoProvider(VideoProvider):
    """Pollinations video beta T2V provider (free, no auth).

    Parameters
    ----------
    client:
        Optional pre-built :class:`httpx.AsyncClient`. When ``None`` a new
        client is created and owned by this instance.
    model:
        Video model slug passed as ``?model=`` parameter. Defaults to
        ``"wan2.1"`` (the Pollinations video default).
    generate_timeout_s:
        Total request timeout for ``generate()``. Pollinations video can
        take 30-90 s; the default of 120 s is intentionally generous.
    """

    name = "pollinations"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        model: str = _DEFAULT_MODEL,
        generate_timeout_s: float = _DEFAULT_GENERATE_TIMEOUT_S,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=generate_timeout_s)
        self._owns_client = client is None
        self._model = model
        self._generate_timeout_s = generate_timeout_s

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_url(self, req: VideoRequest) -> str:
        encoded = urllib.parse.quote(req.prompt, safe="")
        params = (
            f"?seed={req.seed}"
            f"&model={self._model}"
            f"&width={req.width}"
            f"&height={req.height}"
        )
        return f"{_BASE_URL}{_PROMPT_PATH}/{encoded}{params}"

    # ------------------------------------------------------------------
    # VideoProvider ABC
    # ------------------------------------------------------------------

    async def health(self) -> bool:
        """GET the Pollinations video root; True when status < 500.

        Network errors return False without raising.
        """
        try:
            resp = await self._client.get(
                f"{_BASE_URL}{_HEALTH_PATH}", timeout=_HEALTH_TIMEOUT_S
            )
        except (httpx.HTTPError, OSError):
            return False
        return resp.status_code < 500

    async def generate(self, req: VideoRequest) -> VideoResult:
        url = self._build_url(req)
        t0 = time.perf_counter()

        try:
            resp = await self._client.get(url, timeout=self._generate_timeout_s)
        except httpx.TimeoutException as exc:
            raise VideoProviderUnavailable(
                f"pollinations timeout after {self._generate_timeout_s}s"
            ) from exc
        except (httpx.HTTPError, OSError) as exc:
            raise VideoProviderUnavailable(
                f"pollinations transport error: {exc!r}"
            ) from exc

        latency_ms = int((time.perf_counter() - t0) * 1000)

        if resp.status_code == 429:
            raise VideoProviderRateLimited(
                f"pollinations 429: {resp.text[:120]!r}"
            )
        if resp.status_code == 402:
            # x402 queue-full on free tier — semantic rate limit
            raise VideoProviderRateLimited(
                f"pollinations 402 x402-queue-full: {resp.text[:160]!r}"
            )
        if 500 <= resp.status_code < 600:
            raise VideoProviderUnavailable(
                f"pollinations {resp.status_code}: {resp.text[:120]!r}"
            )
        if resp.status_code >= 400:
            raise VideoProviderError(
                f"pollinations {resp.status_code}: {resp.text[:120]!r}"
            )

        data = resp.content
        if not data:
            raise VideoProviderInvalidOutput("pollinations returned empty body")

        ct = resp.headers.get("content-type", "")
        if ct and "video" not in ct.lower():
            raise VideoProviderInvalidOutput(
                f"pollinations non-video content-type: {ct!r}"
            )

        actual_duration = _parse_mp4_duration(data)
        min_duration = req.duration_s * 0.8
        if actual_duration < min_duration:
            raise VideoProviderInvalidOutput(
                f"pollinations clip too short: {actual_duration:.2f}s "
                f"< {min_duration:.2f}s (80% of {req.duration_s}s)"
            )

        return VideoResult(
            bytes_=data,
            mime_type="video/mp4",
            provider=self.name,
            model=self._model,
            duration_s=actual_duration,
            frames_count=0,
            latency_ms=latency_ms,
            cost_usd=0.0,
        )

    @property
    def capabilities(self) -> dict[str, Any]:
        return {
            "max_duration_s": 5.0,
            "supported_resolutions": [(512, 512), (768, 512), (512, 768)],
            "supported_fps": [24],
        }
