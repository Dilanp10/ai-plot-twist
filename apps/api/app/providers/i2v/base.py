"""Image-to-Video provider base — ABC + typed exceptions + request/result.

Delta 008 / Delta 012.

Parallel to ``app.providers.video.base`` (T2V) but the primary input is a
character photo URL rather than a text prompt.  The motion prompt
(``visual_prompt`` from ``ScriptwriterResponseV3.scene``) guides the action.

Typed exception hierarchy lets ``ImageToVideoProviderRouter`` (router.py)
apply the same fallback semantics as the T2V router:

  - ``I2VRateLimited``    → skip, try next immediately
  - ``I2VUnavailable``    → retry with backoff, then fall through
  - ``I2VInvalidOutput``  → skip (no retry)
  - ``I2VProviderError``  → base class for any other failure
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class I2VProviderError(Exception):
    """Base class for all I2V-provider failures."""


class I2VRateLimited(I2VProviderError):
    """Provider quota or rate limit exhausted."""


class I2VUnavailable(I2VProviderError):
    """Provider unreachable or returned 5xx."""


class I2VInvalidOutput(I2VProviderError):
    """Provider responded but video did not pass validation."""


# ---------------------------------------------------------------------------
# Request + result
# ---------------------------------------------------------------------------

_Aspect = Literal["9:16", "16:9", "1:1"]


@dataclass(frozen=True)
class I2VRequest:
    """One image-to-video generation call.

    Attributes
    ----------
    image_url:
        Public HTTPS URL of the character photo (R2 CDN URL).
    motion_prompt:
        English motion description from ``ScriptwriterResponseV3.scene.visual_prompt``.
    duration_s:
        Requested clip duration in seconds.  Kling Standard supports up to 10 s.
    aspect:
        Target aspect ratio.  Defaults to ``"9:16"`` (portrait, mobile-first).
    seed:
        Deterministic seed derived from ``(chapter_id, "i2v")``.
        Providers without seed support ignore this field.
    """

    image_url: str
    motion_prompt: str
    duration_s: float = 10.0
    aspect: _Aspect = "9:16"
    seed: int = 0


@dataclass(frozen=True)
class I2VResult:
    """Outcome of a successful :meth:`ImageToVideoProvider.generate` call.

    Attributes
    ----------
    bytes_:
        Raw MP4 bytes ready to write to a temp file and upload to R2.
    provider:
        Canonical provider identifier (``"kling"``, ``"fake"``).
    model:
        Exact model string used (``"kling-v2-master"``, ``"fake"``, …).
    duration_s:
        Actual clip duration in seconds (≥ ``I2VRequest.duration_s * 0.8``).
    latency_ms:
        End-to-end wall-clock latency in milliseconds.
    cost_usd:
        Estimated USD cost.  ``0.0`` for FakeProvider.
    """

    bytes_: bytes
    provider: str
    model: str
    duration_s: float
    latency_ms: int
    cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# Provider ABC
# ---------------------------------------------------------------------------


class ImageToVideoProvider(ABC):
    """Async interface for every concrete I2V provider.

    Implementations do NOT retry internally.  ``ImageToVideoProviderRouter``
    owns the retry/backoff policy.
    """

    name: str

    @abstractmethod
    async def health(self) -> bool:
        """Lightweight reachability probe.

        Returns ``True`` when the provider's API is reachable.
        MUST NOT raise; catch internal errors and return ``False``.
        """

    @abstractmethod
    async def generate(self, req: I2VRequest) -> I2VResult:
        """Generate one video clip from an image.

        Raises
        ------
        I2VRateLimited
            Provider returned 429 or signaled quota exhausted.
        I2VUnavailable
            Provider returned 5xx, a connect/read timeout, or cold-start 503.
        I2VInvalidOutput
            Response body was empty, wrong MIME, corrupt MP4, or
            actual duration < 80 % of ``req.duration_s``.
        I2VProviderError
            Any other failure (auth, malformed credentials, …).
        """

    @property
    @abstractmethod
    def capabilities(self) -> dict[str, Any]:
        """Report supported features.

        Required keys:

        * ``max_duration_s: float``
        * ``supported_aspects: list[str]``
        """
