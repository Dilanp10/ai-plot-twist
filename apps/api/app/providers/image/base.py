"""Image provider base — ABC + typed exceptions + request/result dataclasses.

Module 009 / Task T-001.

The interface is intentionally narrow: a single ``generate`` method that
returns a frozen :class:`ImageResult` plus a ``health`` probe and a
``capabilities`` dict. No streaming, no batched requests: module 008's
panel pipeline drives parallelism at the orchestrator layer instead.

Typed exception hierarchy lets the :class:`ImageProviderRouter` (T-005)
implement the fallback semantics from FR-005:
  - ``RateLimited``     → skip this provider, try next
  - ``Unavailable``     → retry with backoff up to ``T2I_MAX_RETRIES``,
                         then fall through to next provider
  - ``InvalidOutput``   → skip (no retry — bad prompt or wrong content-type)
  - any other ``Error`` → bubble to caller (operator must intervene)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class ImageProviderError(Exception):
    """Base class for all image-provider failures."""


class ImageProviderRateLimited(ImageProviderError):
    """Provider quota/rate exhausted (HTTP 429 or equivalent).

    Router policy: skip this provider on this attempt; try the next.
    """


class ImageProviderUnavailable(ImageProviderError):
    """Provider unreachable, returned 5xx, or self-reported cold-start.

    Router policy: retry with backoff (up to ``T2I_MAX_RETRIES``), then
    fall through to the next provider.
    """


class ImageProviderInvalidOutput(ImageProviderError):
    """Provider responded but the body was not a valid image.

    Triggered by empty bytes, a wrong ``Content-Type``, or a corrupt
    decode. Router policy: skip this provider (no retry) — re-firing
    the same prompt will almost certainly yield the same broken bytes.
    """


# ---------------------------------------------------------------------------
# Request + result
# ---------------------------------------------------------------------------


_Aspect = Literal["1:1", "16:9", "9:16"]
_MimeType = Literal["image/webp", "image/png", "image/jpeg"]


@dataclass(frozen=True)
class ImageRequest:
    """One image generation call.

    Attributes
    ----------
    prompt:
        Already-composed visual prompt (the consumer concatenates
        visual_prompt + style + negatives upstream).
    seed:
        Derived from ``hash(chapter_id, panel_idx)`` so re-renders are
        deterministic. Providers without seed support ignore this.
    width / height:
        Target dimensions in pixels. Providers may clamp to their own
        supported set; the router does not enforce.
    aspect:
        Symbolic ratio for providers that prefer enums over pixel sizes.
    style_tag:
        Optional provider-specific style hint (e.g. ``"flux"``,
        ``"sdxl-cinematic"``). ``None`` means "use the provider's
        default".
    """

    prompt: str
    seed: int
    width: int = 1024
    height: int = 1024
    aspect: _Aspect = "1:1"
    style_tag: str | None = None


@dataclass(frozen=True)
class ImageResult:
    """Outcome of a successful :meth:`ImageProvider.generate` call.

    Attributes
    ----------
    bytes_:
        Raw image bytes ready to upload to R2.
    mime_type:
        Authoritative content type. The router validates this before
        accepting the result; arbitrary types are not allowed.
    provider:
        Canonical provider identifier (``"pollinations"``, ``"hf"``,
        ``"fake"``). Mirrors :attr:`ImageProvider.name`.
    model:
        Exact model string used (``"flux"``, ``"FLUX.1-schnell"``,
        ``"fake:1x1-png"``, ...).
    latency_ms:
        End-to-end wall-clock latency including transport + decode.
    cost_usd:
        Estimated USD cost of the call. ``0.0`` for free tiers and
        the Fake provider.
    """

    bytes_: bytes
    mime_type: _MimeType
    provider: str
    model: str
    latency_ms: int
    cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# Provider ABC
# ---------------------------------------------------------------------------


class ImageProvider(ABC):
    """Async interface implemented by every concrete image provider.

    Subclasses MUST set ``name`` as a class attribute and override
    :meth:`health`, :meth:`generate`, and :attr:`capabilities`.
    Constructors typically take credentials and an :class:`httpx.AsyncClient`.

    Implementations DO NOT retry internally; the :class:`ImageProviderRouter`
    owns the retry/backoff policy so the same primitive can be tuned
    centrally per environment.
    """

    name: str

    @abstractmethod
    async def health(self) -> bool:
        """Lightweight reachability probe.

        Returns ``True`` when the provider's API is reachable. The
        router skips a provider whose ``health`` returns ``False``
        without attempting a generate call.

        Implementations should cap latency around 2 s so a flaky
        provider does not stall the chain walk.
        """

    @abstractmethod
    async def generate(self, req: ImageRequest) -> ImageResult:
        """Generate one image.

        Raises
        ------
        ImageProviderRateLimited
            Provider returned 429 or signaled quota exhausted.
        ImageProviderUnavailable
            Provider returned 5xx, a connect/read timeout, or a
            self-reported cold start.
        ImageProviderInvalidOutput
            Response body was empty, had the wrong Content-Type, or
            could not be decoded as an image.
        ImageProviderError
            Anything else (auth failure, malformed credentials, etc.).
        """

    @property
    @abstractmethod
    def capabilities(self) -> dict[str, Any]:
        """Report supported features.

        Suggested keys:
          - ``max_resolution: tuple[int, int]`` — largest (w, h) the
            provider will honor.
          - ``supports_seed: bool``.
          - ``supported_models: list[str]``.

        The router does not consume this today; it exists so future
        chain-selection logic can pick a provider that supports the
        request's aspect or resolution without trial-and-error.
        """
