"""PollinationsProvider — text-to-image via image.pollinations.ai.

Module 009 / Task T-003.

Pollinations is a free, no-auth HTTP service that returns a generated
image directly from a URL. URL pattern is defined in SDD §4.4:

  https://image.pollinations.ai/prompt/{enc}?width=W&height=H
                                              &seed=S
                                              &model=flux
                                              &nologo=true
                                              &enhance=false

Exception mapping (router policy lives in :class:`ImageProviderRouter`):

  HTTP 429 / explicit rate-limit body → ``ImageProviderRateLimited``
  HTTP 5xx / timeout / connect error  → ``ImageProviderUnavailable``
  Non-image ``Content-Type`` / 0 bytes → ``ImageProviderInvalidOutput``
  Other (auth misconfiguration, 4xx)  → ``ImageProviderError``
"""

from __future__ import annotations

import time
import urllib.parse
from typing import Any, cast

import httpx

from app.providers.image.base import (
    ImageProvider,
    ImageProviderError,
    ImageProviderInvalidOutput,
    ImageProviderRateLimited,
    ImageProviderUnavailable,
    ImageRequest,
    ImageResult,
)

_BASE_URL = "https://image.pollinations.ai"
_HEALTH_PATH = "/"
_PROMPT_PATH = "/prompt"
_DEFAULT_MODEL = "flux"
_HEALTH_TIMEOUT_S = 2.0
_GENERATE_TIMEOUT_S = 60.0


def _mime_to_literal(mime: str) -> str | None:
    """Map a raw Content-Type to one of the three allowed literals.

    Returns ``None`` when the type is not an image we will accept.
    """
    canon = mime.split(";", 1)[0].strip().lower()
    if canon in ("image/webp", "image/png", "image/jpeg"):
        return canon
    return None


class PollinationsProvider(ImageProvider):
    """Free no-auth T2I provider; default primary in MVP."""

    name = "pollinations"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        model: str = _DEFAULT_MODEL,
        generate_timeout_s: float = _GENERATE_TIMEOUT_S,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=generate_timeout_s)
        self._owns_client = client is None
        self._model = model
        self._generate_timeout_s = generate_timeout_s

    async def aclose(self) -> None:
        """Close the underlying HTTP client when this provider owns it."""
        if self._owns_client:
            await self._client.aclose()

    # -----------------------------------------------------------------------
    # health
    # -----------------------------------------------------------------------

    async def health(self) -> bool:
        """GET / with a 2-second cap; True when status < 500.

        Network errors and timeouts both return False so the router can
        skip this provider cleanly without inspecting exceptions.
        """
        try:
            resp = await self._client.get(
                f"{_BASE_URL}{_HEALTH_PATH}", timeout=_HEALTH_TIMEOUT_S
            )
        except (httpx.HTTPError, OSError):
            return False
        return resp.status_code < 500

    # -----------------------------------------------------------------------
    # generate
    # -----------------------------------------------------------------------

    def _build_url(self, req: ImageRequest) -> str:
        encoded = urllib.parse.quote(req.prompt, safe="")
        params = (
            f"?width={req.width}"
            f"&height={req.height}"
            f"&seed={req.seed}"
            f"&model={self._model}"
            f"&nologo=true"
            f"&enhance=false"
        )
        return f"{_BASE_URL}{_PROMPT_PATH}/{encoded}{params}"

    async def generate(self, req: ImageRequest) -> ImageResult:
        url = self._build_url(req)
        t0 = time.perf_counter()

        try:
            resp = await self._client.get(url, timeout=self._generate_timeout_s)
        except httpx.TimeoutException as exc:
            raise ImageProviderUnavailable(
                f"pollinations timeout after {self._generate_timeout_s}s"
            ) from exc
        except (httpx.HTTPError, OSError) as exc:
            raise ImageProviderUnavailable(
                f"pollinations transport error: {exc!r}"
            ) from exc

        latency_ms = int((time.perf_counter() - t0) * 1000)

        if resp.status_code == 429:
            raise ImageProviderRateLimited(
                f"pollinations 429: {resp.text[:120]!r}"
            )
        if 500 <= resp.status_code < 600:
            raise ImageProviderUnavailable(
                f"pollinations {resp.status_code}: {resp.text[:120]!r}"
            )
        if resp.status_code >= 400:
            raise ImageProviderError(
                f"pollinations {resp.status_code}: {resp.text[:120]!r}"
            )

        content_type = resp.headers.get("content-type", "")
        mime_literal = _mime_to_literal(content_type)
        if mime_literal is None:
            raise ImageProviderInvalidOutput(
                f"pollinations returned non-image content-type: {content_type!r}"
            )

        if not resp.content:
            raise ImageProviderInvalidOutput(
                "pollinations returned an empty body"
            )

        return ImageResult(
            bytes_=resp.content,
            mime_type=cast(Any, mime_literal),
            provider=self.name,
            model=self._model,
            latency_ms=latency_ms,
            cost_usd=0.0,
        )

    @property
    def capabilities(self) -> dict[str, Any]:
        return {
            "max_resolution": (1536, 1536),
            "supports_seed": True,
            "supported_models": ["flux", "sdxl", "turbo"],
        }
