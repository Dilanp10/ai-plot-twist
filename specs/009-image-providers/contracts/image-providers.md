# Internal Contract: `ImageProvider` Abstraction

**Module**: `009-image-providers` | **Date**: 2026-06-07

This is an **internal Python contract**, not an HTTP interface. There is no
OpenAPI document because consumers (other modules) import these symbols
directly. The contract is enforced by `mypy --strict` + the import-graph test
in FR-010.

The shape mirrors SDD §4.5.1.

---

## Public types

```python
# app/providers/image/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class ImageRequest:
    prompt: str                       # composed: visual_prompt + style + negatives
    seed: int                         # derived from hash(chapter_id, panel_idx)
    width: int = 1024
    height: int = 1024
    aspect: Literal["1:1", "16:9", "9:16"] = "1:1"
    style_tag: str | None = None      # provider-specific; e.g. "flux", "sdxl-cinematic"

@dataclass(frozen=True)
class ImageResult:
    bytes_: bytes
    mime_type: Literal["image/webp", "image/png", "image/jpeg"]
    provider: str                     # canonical name: "pollinations" | "hf" | "fake"
    model: str                        # "flux", "FLUX.1-schnell", "fake:1x1-png", …
    latency_ms: int
    cost_usd: float = 0.0
```

## Public exceptions

```python
class ImageProviderError(Exception): ...
class ImageProviderRateLimited(ImageProviderError): ...
class ImageProviderUnavailable(ImageProviderError): ...
class ImageProviderInvalidOutput(ImageProviderError): ...
```

Router semantics per exception are documented in research R-002.

## ABC

```python
class ImageProvider(ABC):
    name: str

    @abstractmethod
    async def health(self) -> bool:
        """Lightweight reachability probe. < 2 s. False ⇒ router skips this provider."""

    @abstractmethod
    async def generate(self, req: ImageRequest) -> ImageResult:
        """Generate one image. Raises typed exceptions. DOES NOT retry internally."""

    @property
    @abstractmethod
    def capabilities(self) -> dict:
        """Reports features. Suggested keys: max_resolution, supports_seed,
        supported_models, …"""
```

## Router

```python
class ImageProviderRouter:
    def __init__(
        self,
        chain: list[ImageProvider],
        max_retries_per_provider: int = T2I_MAX_RETRIES,
        backoff_seconds: list[float] = T2I_BACKOFF_SECONDS,
    ): ...

    async def render(self, req: ImageRequest) -> ImageResult:
        """
        Walk `chain` in order:
          - skip if `await provider.health()` is False
          - on RateLimited: skip to next
          - on Unavailable: retry up to `max_retries_per_provider` with backoff
          - on InvalidOutput: skip to next without retry
          - on success: return immediately
        If chain exhausted, raises ImageProviderUnavailable("All providers
        exhausted") chained from the last seen exception.
        """
```

## Factory

```python
# app/providers/image/__init__.py
from typing import Literal

def chain_for_env(
    env: Literal["mvp", "dev", "v02"],
) -> list[ImageProvider]:
    """
    mvp → [PollinationsProvider(), HuggingFaceProvider()]
    dev → [FakeImageProvider()]
    v02 → raises NotImplementedError (LocalComfyProvider reserved)
    """
```

## Path helper

```python
# app/providers/image/paths.py
def compute_r2_path(
    season_slug: str,
    chapter_public_id: str,
    panel_idx: int,
    image_result: ImageResult,
) -> str:
    """Returns 'seasons/{slug}/{uuid}/{idx}-{hash}.{ext}'. Pure, deterministic."""
```

## Consumer rules (enforced by import-graph test)

A module is a **business consumer** if it lives under `app/api/`, `app/domain/`,
or `app/scripts/`. Such modules MUST import only from
`app.providers.image` (the package root) — not from individual provider
sub-modules. Specifically banned imports outside `app.providers.image.*`:

- `httpx.AsyncClient` parameterized with a Pollinations or HF URL.
- The string literals `"image.pollinations.ai"` or `"api-inference.huggingface.co"`.

The test fails any PR that introduces such a literal in a non-provider file.

## Versioning

The public contract is versioned implicitly by the module name (`009-image-
providers`). Breaking changes (renaming `ImageRequest` fields, removing an
exception subclass) require:

1. Bumping a `IMAGE_PROVIDER_API_VERSION` constant.
2. An ADR under `docs/adr/`.
3. Coordinated PRs to every consumer (module 008 + future LocalComfy).

Non-breaking additions (new optional fields, new exception subclasses) are
single-PR changes.
