"""Video provider base — ABC + typed exceptions + request/result dataclasses.

Module 012 / Task T-001.

The interface mirrors ``app.providers.image.base`` with the following
differences:

* :class:`VideoRequest` adds ``duration_s``, ``fps``, and ``frames_count``
  (derived) to drive T2V providers that need an explicit frame budget.
* :class:`VideoResult` adds ``duration_s`` and ``frames_count`` to support the
  80 % duration validation that every concrete provider must enforce before
  returning a result.
* The typed exception hierarchy is parallel but independent from the image
  one — callers can ``except VideoProviderError`` to catch any T2V failure
  without accidentally swallowing image-provider errors.

Typed exception hierarchy lets the :class:`VideoProviderRouter` (T-005)
implement the fallback semantics from FR-005:

  - ``RateLimited``   → skip this provider, try next immediately
  - ``Unavailable``   → retry with backoff up to ``T2V_MAX_RETRIES``,
                        then fall through to next provider
  - ``InvalidOutput`` → skip (no retry — corrupt bytes, wrong MIME,
                        or clip shorter than 80 % of requested duration)
  - ``NotImplementedError`` → propagate immediately (stub in chain)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class VideoProviderError(Exception):
    """Base class for all video-provider failures."""


class VideoProviderRateLimited(VideoProviderError):
    """Provider quota or rate limit exhausted (HTTP 429 or equivalent).

    Router policy: skip this provider immediately; try the next in chain.
    """


class VideoProviderUnavailable(VideoProviderError):
    """Provider unreachable, returned 5xx, or reported a cold-start 503.

    Router policy: retry with exponential backoff (up to
    ``T2V_MAX_RETRIES``), then fall through to the next provider.
    """


class VideoProviderInvalidOutput(VideoProviderError):
    """Provider responded but the clip did not pass validation.

    Triggered by: empty bytes, wrong ``Content-Type``, corrupt MP4,
    or actual duration < 80 % of ``VideoRequest.duration_s``.

    Router policy: skip this provider (no retry) — re-firing the same
    prompt against the same provider almost always produces the same
    broken bytes.
    """


# ---------------------------------------------------------------------------
# Request + result
# ---------------------------------------------------------------------------

_Aspect = Literal["9:16", "16:9", "1:1"]
_VideoMime = Literal["video/mp4"]


@dataclass(frozen=True)
class VideoRequest:
    """One video clip generation call.

    Attributes
    ----------
    prompt:
        Already-composed visual prompt (visual_prompt + style + negatives
        concatenated upstream by the coordinator).
    seed:
        Derived from ``stable_hash(chapter_id, clip.idx)`` so re-renders
        are deterministic. Providers without seed support ignore this.
    duration_s:
        Requested clip length in seconds. Providers may deliver slightly
        less; the router accepts clips ≥ ``duration_s * 0.8``.
    width / height:
        Target dimensions in pixels. Providers may clamp to their own
        supported set; the router does not enforce resolution.
    fps:
        Requested frames-per-second. Used by ``HFVideoProvider`` to derive
        ``num_frames``; other providers may ignore it.
    aspect:
        Symbolic ratio for providers that prefer enum strings over pixel
        sizes. Defaults to ``"9:16"`` (portrait, mobile-first).
    style_tag:
        Optional provider-specific style hint. ``None`` means "use the
        provider's default".
    """

    prompt: str
    seed: int
    duration_s: float = 5.0
    width: int = 512
    height: int = 512
    fps: int = 24
    aspect: _Aspect = "9:16"
    style_tag: str | None = None


@dataclass(frozen=True)
class VideoResult:
    """Outcome of a successful :meth:`VideoProvider.generate` call.

    Attributes
    ----------
    bytes_:
        Raw MP4 bytes ready to write to a temp file and upload to R2.
    mime_type:
        Must be ``"video/mp4"`` — the only accepted MIME in MVP.
    provider:
        Canonical provider identifier (``"hf"``, ``"pollinations"``,
        ``"fake"``). Mirrors :attr:`VideoProvider.name`.
    model:
        Exact model string used (``"ltx-video"``,
        ``"pollinations-video"``, ``"fake"``, …).
    duration_s:
        Actual clip duration in seconds, parsed from the MP4 metadata by
        the provider before returning. Must be ≥
        ``VideoRequest.duration_s * 0.8`` (enforced by each provider;
        the router trusts this value).
    frames_count:
        Actual frame count derived from metadata. Used for observability
        and manifest population; not validated by the router.
    latency_ms:
        End-to-end wall-clock latency including network transport and
        MP4 parsing.
    cost_usd:
        Estimated USD cost of the call. ``0.0`` for free-tier providers
        and the Fake provider.
    """

    bytes_: bytes
    mime_type: _VideoMime
    provider: str
    model: str
    duration_s: float
    frames_count: int
    latency_ms: int
    cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# Provider ABC
# ---------------------------------------------------------------------------


class VideoProvider(ABC):
    """Async interface implemented by every concrete video provider.

    Subclasses MUST set ``name`` as a class attribute and override
    :meth:`health`, :meth:`generate`, and :attr:`capabilities`.
    Constructors typically accept credentials and an
    :class:`httpx.AsyncClient`.

    Implementations DO NOT retry internally. The :class:`VideoProviderRouter`
    (T-005) owns the retry/backoff policy so the same primitive can be
    tuned centrally per environment.

    Paid-provider stubs (``KlingProvider``, ``RunwayProvider``,
    ``LumaProvider``) also subclass this ABC and raise
    :class:`NotImplementedError` on every method — the router propagates
    ``NotImplementedError`` immediately as a misconfiguration signal.
    """

    name: str

    @abstractmethod
    async def health(self) -> bool:
        """Lightweight reachability probe.

        Returns ``True`` when the provider's API is reachable.
        The router skips a provider whose ``health`` returns ``False``
        without consuming a retry slot.

        Implementations MUST NOT raise; catch internal errors and return
        ``False`` instead. Target latency ≤ 2 s.
        """

    @abstractmethod
    async def generate(self, req: VideoRequest) -> VideoResult:
        """Generate one video clip.

        Validates the returned clip's duration against ``req.duration_s``
        (80 % threshold) before constructing :class:`VideoResult`.

        Raises
        ------
        VideoProviderRateLimited
            Provider returned 429 or signaled quota exhausted.
        VideoProviderUnavailable
            Provider returned 5xx, a connect/read timeout, or a
            self-reported cold-start 503.
        VideoProviderInvalidOutput
            Response body was empty, had wrong ``Content-Type``,
            was a corrupt MP4, or actual duration < 80 % of requested.
        VideoProviderError
            Anything else (auth failure, malformed credentials, …).
        """

    @property
    @abstractmethod
    def capabilities(self) -> dict[str, Any]:
        """Report supported features.

        Required keys (enforced by stub tests):

        * ``max_duration_s: float`` — longest clip the provider supports.
        * ``supported_resolutions: list[tuple[int, int]]`` — ``(w, h)``
          pairs the provider will honor without silently clamping.
        * ``supported_fps: list[int]`` — FPS values the provider accepts.

        The router does not consume ``capabilities`` today; it exists so
        future chain-selection logic and tooling can introspect provider
        limits without calling ``generate``.
        """
