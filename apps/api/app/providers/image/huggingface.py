"""HuggingFaceProvider — text-to-image via Hugging Face Inference API.

Module 009 / Task T-004.

Uses ``black-forest-labs/FLUX.1-schnell`` by default — fast, free-tier
friendly, and the same backbone as Pollinations so output style stays
consistent across the fallback chain.

Cold-start handling
-------------------

The Inference API returns ``HTTP 503`` with a JSON body that looks like::

    {"error":"Model black-forest-labs/FLUX.1-schnell is currently loading",
     "estimated_time":18.4}

We map this to :class:`ImageProviderUnavailable` so the router retries
with backoff (which is exactly what HF wants — they expect callers to
wait ``estimated_time`` seconds and re-fire).

Exception mapping (router policy lives in :class:`ImageProviderRouter`):

  HTTP 429                            → ``ImageProviderRateLimited``
  HTTP 5xx / cold-start 503 / timeout → ``ImageProviderUnavailable``
  Non-image ``Content-Type`` / 0 bytes → ``ImageProviderInvalidOutput``
  HTTP 401 / 403                      → ``ImageProviderError`` (auth)
  Other 4xx                           → ``ImageProviderError``
"""

from __future__ import annotations

import time
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

_BASE_URL = "https://api-inference.huggingface.co"
_DEFAULT_MODEL = "black-forest-labs/FLUX.1-schnell"
_HEALTH_TIMEOUT_S = 2.0
_GENERATE_TIMEOUT_S = 120.0


def _mime_to_literal(mime: str) -> str | None:
    canon = mime.split(";", 1)[0].strip().lower()
    if canon in ("image/webp", "image/png", "image/jpeg"):
        return canon
    return None


def _looks_like_cold_start(resp: httpx.Response) -> bool:
    """Detect HF's cold-start 503 by sniffing the body for ``estimated_time``.

    The response is JSON; rather than hard-parse (which might fail on
    odd payloads), substring-match — the field name is stable and the
    cost of a false positive is one extra retry.
    """
    if resp.status_code != 503:
        return False
    body = resp.text[:512]
    return "estimated_time" in body


class HuggingFaceProvider(ImageProvider):
    """HF Inference API T2I provider; default fallback in MVP."""

    name = "hf"

    def __init__(
        self,
        *,
        token: str,
        client: httpx.AsyncClient | None = None,
        model: str = _DEFAULT_MODEL,
        generate_timeout_s: float = _GENERATE_TIMEOUT_S,
    ) -> None:
        if not token:
            raise ValueError("HuggingFaceProvider requires a non-empty token")
        self._token = token
        self._client = client or httpx.AsyncClient(timeout=generate_timeout_s)
        self._owns_client = client is None
        self._model = model
        self._generate_timeout_s = generate_timeout_s

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "image/*",
        }

    # -----------------------------------------------------------------------
    # health
    # -----------------------------------------------------------------------

    async def health(self) -> bool:
        """GET base URL with a 2-second cap; True when status < 500.

        Network errors return False so the router skips this provider
        without inspecting exceptions.
        """
        try:
            resp = await self._client.get(
                _BASE_URL, timeout=_HEALTH_TIMEOUT_S
            )
        except (httpx.HTTPError, OSError):
            return False
        return resp.status_code < 500

    # -----------------------------------------------------------------------
    # generate
    # -----------------------------------------------------------------------

    def _body(self, req: ImageRequest) -> dict[str, Any]:
        return {
            "inputs": req.prompt,
            "parameters": {
                "seed": req.seed,
                "width": req.width,
                "height": req.height,
            },
        }

    async def generate(self, req: ImageRequest) -> ImageResult:
        url = f"{_BASE_URL}/models/{self._model}"
        t0 = time.perf_counter()

        try:
            resp = await self._client.post(
                url,
                json=self._body(req),
                headers=self._headers(),
                timeout=self._generate_timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise ImageProviderUnavailable(
                f"hf timeout after {self._generate_timeout_s}s"
            ) from exc
        except (httpx.HTTPError, OSError) as exc:
            raise ImageProviderUnavailable(
                f"hf transport error: {exc!r}"
            ) from exc

        latency_ms = int((time.perf_counter() - t0) * 1000)

        if resp.status_code == 429:
            raise ImageProviderRateLimited(f"hf 429: {resp.text[:120]!r}")

        if _looks_like_cold_start(resp):
            raise ImageProviderUnavailable(
                f"hf cold start (503): {resp.text[:160]!r}"
            )

        if 500 <= resp.status_code < 600:
            raise ImageProviderUnavailable(
                f"hf {resp.status_code}: {resp.text[:120]!r}"
            )

        if resp.status_code in (401, 403):
            raise ImageProviderError(
                f"hf auth failure {resp.status_code}: {resp.text[:120]!r}"
            )

        if resp.status_code >= 400:
            raise ImageProviderError(
                f"hf {resp.status_code}: {resp.text[:120]!r}"
            )

        content_type = resp.headers.get("content-type", "")
        mime_literal = _mime_to_literal(content_type)
        if mime_literal is None:
            raise ImageProviderInvalidOutput(
                f"hf returned non-image content-type: {content_type!r}"
            )
        if not resp.content:
            raise ImageProviderInvalidOutput("hf returned an empty body")

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
            "max_resolution": (1024, 1024),
            "supports_seed": True,
            "supported_models": [
                "black-forest-labs/FLUX.1-schnell",
                "stabilityai/stable-diffusion-xl-base-1.0",
            ],
        }
