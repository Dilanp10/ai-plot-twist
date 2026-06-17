# Internal Contract: `VideoProvider` Abstraction

**Module**: `012-video-providers` | **Date**: 2026-06-16

This is an **internal Python contract**, not an HTTP interface. There is no
OpenAPI document because consumers (other modules) import these symbols
directly. The contract is enforced by `mypy --strict` + the import-graph test
in FR-011.

The shape mirrors SDD Ronda 6 decisions #22-27. Parallel to module 009's
`ImageProvider` contract.

---

## Public types

```python
# app/providers/video/base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class VideoRequest:
    prompt: str                                 # composed visual+narrative prompt
    seed: int                                   # derived from hash(chapter_id, clip_idx)
    duration_s: float = 5.0                     # requested clip length in seconds
    width: int = 512
    height: int = 512
    fps: int = 24
    aspect: Literal["9:16", "16:9", "1:1"] = "9:16"   # portrait-first for mobile
    style_tag: str | None = None                # provider-specific hint


@dataclass(frozen=True)
class VideoResult:
    bytes_: bytes
    mime_type: Literal["video/mp4"]             # only accepted MIME in MVP
    provider: str                               # "hf" | "pollinations" | "fake"
    model: str                                  # "ltx-video" | "pollinations-video" | "fake"
    duration_s: float                           # actual duration parsed from mp4 metadata
    frames_count: int                           # actual frame count
    latency_ms: int
    cost_usd: float = 0.0
```

**`VideoRequest.duration_s` → `num_frames` derivation** (HF LTX-Video
constraint: `num_frames % 8 == 1`):

```python
def _derive_num_frames(duration_s: float, fps: int) -> int:
    """Round to nearest valid LTX-Video frame count (n*8 + 1, n >= 1)."""
    raw = round(duration_s * fps)
    n = max(1, round((raw - 1) / 8))
    return n * 8 + 1
# Examples: duration_s=5.0, fps=24 → raw=120 → n=15 → num_frames=121
#           duration_s=4.0, fps=24 → raw=96  → n=12 → num_frames=97
#           duration_s=2.0, fps=24 → raw=48  → n=6  → num_frames=49
```

This helper lives in `app/providers/video/hf.py` (internal to the provider),
not on `VideoRequest` itself.

---

## Public exceptions

```python
# app/providers/video/base.py

class VideoProviderError(Exception): ...
class VideoProviderRateLimited(VideoProviderError): ...
class VideoProviderUnavailable(VideoProviderError): ...
class VideoProviderInvalidOutput(VideoProviderError): ...
```

Router semantics per exception (from research R-004):

| Exception | Trigger | Router behavior |
|---|---|---|
| `VideoProviderRateLimited` | HTTP 429; community queue full | Skip to next provider immediately |
| `VideoProviderUnavailable` | HTTP 5xx; network timeout; model cold-start 503 | Retry with exponential backoff |
| `VideoProviderInvalidOutput` | Non-video MIME; 0 bytes; duration < 80% of requested | Skip to next; no retry |
| `VideoProviderError` (base) | Generic; should not be raised directly | Propagated as-is |
| `NotImplementedError` | Stub provider called | Propagated immediately — misconfigured chain |

---

## ABC

```python
# app/providers/video/base.py

class VideoProvider(ABC):
    name: str                           # declared as class attribute on each impl

    @abstractmethod
    async def health(self) -> bool:
        """
        Lightweight reachability probe. Must return within 2 s.
        False ⇒ router skips this provider without consuming a retry slot.
        Should NOT raise; catch internal errors and return False.
        """

    @abstractmethod
    async def generate(self, req: VideoRequest) -> VideoResult:
        """
        Generate one video clip.
        Raises typed VideoProviderError subclasses. DOES NOT retry internally.
        Validates duration: raises VideoProviderInvalidOutput if
        actual_duration_s < req.duration_s * 0.8.
        """

    @property
    @abstractmethod
    def capabilities(self) -> dict:
        """
        Reports provider features. Required keys:
          max_duration_s: float
          supported_resolutions: list[tuple[int, int]]
          supported_fps: list[int]
        Used by tooling and stub introspection; not called by the router.
        """
```

---

## Router

```python
# app/providers/video/router.py
from app.providers.video.base import VideoProvider, VideoRequest, VideoResult
from app.core.config import settings


class VideoProviderRouter:
    def __init__(
        self,
        chain: list[VideoProvider],
        max_retries_per_provider: int = settings.T2V_MAX_RETRIES,
        backoff_seconds: list[float] = settings.T2V_BACKOFF_SECONDS,
    ) -> None: ...

    async def render(self, req: VideoRequest) -> VideoResult:
        """
        Walk `chain` in order. For each provider:
          1. Call await provider.health(). If False → log video_provider_skipped,
             continue to next.
          2. Attempt generate(req) up to max_retries_per_provider times:
               - RateLimited  → log attempt(outcome=rate_limited), break inner
                                loop, continue to next provider.
               - Unavailable  → log attempt(outcome=unavailable), sleep
                                backoff_seconds[attempt], retry if slots remain;
                                else continue to next provider.
               - InvalidOutput → log attempt(outcome=invalid_output), break
                                 inner loop (no retry), continue to next provider.
               - NotImplementedError → propagate immediately (misconfigured chain).
               - Success → log attempt(outcome=success), return VideoResult.
          3. On first provider switch: log video_provider_failover{from_, to, reason}.
        If chain exhausted: raise VideoProviderUnavailable("All providers
        exhausted") chained from the last seen exception.
        """
```

---

## Factory

```python
# app/providers/video/__init__.py
from typing import Literal
from app.providers.video.base import VideoProvider


def chain_for_env(
    env: Literal["mvp", "dev", "paid_v1"],
) -> list[VideoProvider]:
    """
    mvp     → [HFVideoProvider(), PollinationsVideoProvider()]
    dev     → [FakeVideoProvider()]
    paid_v1 → raises NotImplementedError (KlingProvider / RunwayProvider /
               LumaProvider stubs; real implementation pending paid-T2V module)
    """
```

---

## Path helper

```python
# app/providers/video/paths.py
from app.providers.video.base import VideoResult


def compute_r2_clip_path(
    season_slug: str,
    chapter_public_id: str,     # UUID as str, lowercase with hyphens
    clip_idx: int,               # 0-indexed
    video_result: VideoResult,
) -> str:
    """
    Returns 'seasons/{slug}/{uuid}/clips/{idx}-{sha256(bytes_)[:8]}.mp4'.
    Pure, deterministic, content-addressed.
    """
```

---

## Paid provider stubs

```python
# app/providers/video/kling.py
class KlingProvider(VideoProvider):
    name = "kling"

    @property
    def capabilities(self) -> dict:
        return {
            "max_duration_s": 10,
            "supported_resolutions": [(1280, 720), (1920, 1080)],
            "supported_fps": [24, 30],
        }

    async def health(self) -> bool:
        raise NotImplementedError(
            "KlingProvider stub — ver SDD Ronda 6 #26. "
            "Implementar cuando exista plan paid-T2V (módulo futuro 013)."
        )

    async def generate(self, req: VideoRequest) -> VideoResult:
        raise NotImplementedError(
            "KlingProvider stub — ver SDD Ronda 6 #26."
        )


# app/providers/video/runway.py
class RunwayProvider(VideoProvider):
    name = "runway"

    @property
    def capabilities(self) -> dict:
        return {
            "max_duration_s": 16,
            "supported_resolutions": [(1280, 768), (1920, 1080)],
            "supported_fps": [24],
        }

    async def health(self) -> bool:
        raise NotImplementedError(
            "RunwayProvider stub — ver SDD Ronda 6 #26."
        )

    async def generate(self, req: VideoRequest) -> VideoResult:
        raise NotImplementedError(
            "RunwayProvider stub — ver SDD Ronda 6 #26."
        )


# app/providers/video/luma.py
class LumaProvider(VideoProvider):
    name = "luma"

    @property
    def capabilities(self) -> dict:
        return {
            "max_duration_s": 9,
            "supported_resolutions": [(1360, 752), (1920, 1080)],
            "supported_fps": [24, 30],
        }

    async def health(self) -> bool:
        raise NotImplementedError(
            "LumaProvider stub — ver SDD Ronda 6 #26."
        )

    async def generate(self, req: VideoRequest) -> VideoResult:
        raise NotImplementedError(
            "LumaProvider stub — ver SDD Ronda 6 #26."
        )
```

**Why `health()` raises instead of returning `False`**: returning `False` would
silently skip the stub in a misconfigured chain. `NotImplementedError` propagates
loudly, making the misconfiguration immediately visible in staging.

**Why `capabilities` is populated on stubs**: allows tooling to introspect the
expected provider surface (resolution limits, FPS, max duration) without calling
`generate()`. Useful for future compatibility checks when planning the paid-T2V
module.

---

## Consumer rules (enforced by import-graph test — FR-011)

A module is a **business consumer** if it lives under `app/api/`, `app/domain/`,
or `app/scripts/`. Such modules MUST import only from `app.providers.video`
(the package root) — never from individual provider sub-modules. Specifically
banned imports outside `app.providers.video.*`:

- `httpx.AsyncClient` parameterized with a Pollinations video or HF URL.
- The string literals `"video.pollinations.ai"` or
  `"api-inference.huggingface.co/models/Lightricks"`.

The import-graph test fails any PR that introduces such a literal in a
non-provider file.

---

## Settings entries (new, added to `app/core/config.py`)

```python
# All values configurable via env vars / .env
T2V_TIMEOUT_S: int = 300                        # per HTTP request to provider
T2V_MAX_RETRIES: int = 3                        # per provider on Unavailable
T2V_BACKOFF_SECONDS: list[int] = [5, 15, 45]   # indexed by retry attempt (0,1,2)
# Parseable from CSV env var: T2V_BACKOFF_SECONDS_CSV="5,15,45"
```

These settings live alongside `T2I_TIMEOUT_S` / `T2I_MAX_RETRIES` /
`T2I_BACKOFF_SECONDS` introduced by module 009. No naming collision.

---

## Versioning

The public contract is versioned implicitly by the module name
(`012-video-providers`). Breaking changes (renaming `VideoRequest` fields,
removing an exception subclass, changing `VideoResult.duration_s` semantics)
require:

1. Bumping a `VIDEO_PROVIDER_API_VERSION` constant in `app/providers/video/base.py`.
2. An ADR under `docs/adr/`.
3. Coordinated PRs to every consumer (module 008 delta + future paid-T2V module).

Non-breaking additions (new optional fields on `VideoRequest`, new exception
subclasses) are single-PR changes.
