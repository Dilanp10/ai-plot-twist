# Feature Specification: ImageProvider Abstraction

**Feature Branch**: `009-image-providers`
**Created**: 2026-06-07
**Status**: Draft
**Depends on**: `001-project-bootstrap`

## Summary

Ship the `ImageProvider` abstraction defined in SDD §4.5: an abstract base class
with typed exceptions, two production implementations (`PollinationsProvider`,
`HuggingFaceProvider`), a fallback `ImageProviderRouter` with chain semantics, and
a `FakeImageProvider` for tests. The router enforces health-gating, per-provider
exponential backoff retries, and provider-specific exception semantics
(`RateLimited` → skip, `Unavailable` → retry, `InvalidOutput` → skip without
retry).

No HTTP endpoints. No DB writes. No FSM integration. This module is **pure
infrastructure** that module 008 (generation-pipeline) imports.

The third implementation listed in SDD §4.5 — `LocalComfyProvider` — is **NOT
shipped in this module**. It is reserved for v0.2 (when GPU local availability
returns). The abstraction is designed so adding it is a config-only change.

## User Scenarios & Testing

### User Story 1 — A consumer renders an image via the router (Priority: P1)

Module 008 imports `ImageProviderRouter`, calls `router.render(ImageRequest)`,
gets back `ImageResult` with bytes. Pollinations succeeds on first try in the
common case.

**Why this priority**: this is the entire purpose of the module. Module 008
cannot generate chapter panels without it.

**Independent Test**: unit test with `FakeImageProvider` returning a 1×1 PNG;
integration test with real Pollinations behind `@pytest.mark.live`.

**Acceptance Scenarios**:

1. **Given** a router with chain `[Pollinations, HuggingFace]` and both healthy,
   **When** the consumer calls `router.render(ImageRequest(prompt="…", seed=42,
   width=1024, height=1024))`,
   **Then** Pollinations is tried first, returns `ImageResult(bytes_, "image/webp",
   "pollinations", "flux", latency_ms, cost_usd=0)`, the router emits an
   `image_provider_attempt {provider:"pollinations", attempt:0, outcome:"success"}`
   log.

2. **Given** the request includes a seed,
   **When** the same request is rendered twice,
   **Then** Pollinations returns the same image (deterministic seed).

### User Story 2 — Failover from Pollinations to HuggingFace (Priority: P1)

Pollinations returns 429 (rate limited). The router skips to HuggingFace without
exhausting retries on the dead provider.

**Acceptance Scenarios**:

1. **Given** `PollinationsProvider` is mocked to raise `ImageProviderRateLimited`,
   **When** the router renders,
   **Then** Pollinations is tried once; on `RateLimited`, the router skips
   directly to HuggingFace. Logs include `image_provider_attempt {provider:"pollinations",
   outcome:"rate_limited"}` followed by `image_provider_attempt
   {provider:"hf", attempt:0, outcome:"success"}`.

2. **Given** `PollinationsProvider` raises `ImageProviderUnavailable` on every
   attempt,
   **When** the router renders,
   **Then** Pollinations is tried `T2I_MAX_RETRIES` times with exponential
   backoff (2 s, 6 s, 18 s); then HuggingFace is tried. Total time bounded by
   ≤ `(2 + 6 + 18) + HF_first_attempt_latency`.

3. **Given** both providers are unavailable through all retries,
   **When** the router renders,
   **Then** it raises `ImageProviderUnavailable("All providers exhausted")` with
   the last exception chained (`raise … from`). No partial result; no panic.

### User Story 3 — Health gate skips dead providers without consuming retries (Priority: P2)

If a provider's `health()` returns False, the router doesn't even attempt a render
against it.

**Acceptance Scenarios**:

1. **Given** Pollinations `.health()` returns False,
   **When** the router renders,
   **Then** Pollinations is skipped (no `generate` call), router moves to
   HuggingFace. Log: `image_provider_skipped {provider:"pollinations",
   reason:"health_false"}`.

### User Story 4 — `InvalidOutput` fails fast without retry (Priority: P2)

The provider returns a payload that doesn't match `ImageResult` invariants
(e.g., non-image MIME, zero bytes). The router treats this as a hard fail for
that provider — no retry, immediate skip to the next.

**Why this priority**: retrying on `InvalidOutput` is usually pointless (the
provider's bug, not transient), and the spec must be unambiguous.

**Acceptance Scenarios**:

1. **Given** Pollinations returns a 200 with `Content-Type: text/html` (CDN error
   page) and the provider's parser raises `ImageProviderInvalidOutput`,
   **When** the router renders,
   **Then** Pollinations is attempted **once** (attempt 0), no retries, router
   moves to HuggingFace.

### User Story 5 — Asset path derivation (Priority: P2)

Module 008 uploads the result to R2. The path scheme is the consumer's
responsibility, but the abstraction provides a helper to compute a deterministic
path from `ImageRequest` + content hash.

**Acceptance Scenarios**:

1. **Given** an `ImageResult` with `bytes_`,
   **When** the consumer calls `compute_r2_path(season_slug, chapter_public_id,
   panel_idx, image_result)`,
   **Then** the returned path is
   `seasons/{season_slug}/{chapter_public_id}/{panel_idx}-{sha256(bytes_)[:8]}.{ext}`
   where ext is derived from `mime_type`. Same input → same path.

### Edge Cases

- **Provider returns 0-byte response**: parser raises `InvalidOutput`.
- **Provider returns >> requested resolution**: accepted as-is. The consumer
  may downscale if it cares. (Module 008 doesn't; R2 + browser scaling.)
- **Provider returns a watermarked image despite `nologo=true`**: not detectable
  by the router. Documented as known limitation; future work could add a CLIP-
  based watermark detector behind `InvalidOutput`.
- **Network timeout > `T2I_TIMEOUT_S`**: provider raises
  `ImageProviderUnavailable("timeout")`; router retries with backoff.
- **All providers healthy but every attempt times out**: bounded by the retry
  count + timeout per provider; total wait ≤ ~10 min in the worst case. Module
  008's `PIPELINE_HARD_DEADLINE_S` (55 min) is the hard bound.
- **Concurrent calls to the same router**: each call has its own attempt cursor;
  no shared mutable state. Tested with `asyncio.gather`.

## Requirements

### Functional Requirements

- **FR-001**: `app/providers/image/base.py` defines:
  - `ImageRequest` (frozen dataclass with `prompt, seed, width, height, aspect,
    style_tag`).
  - `ImageResult` (frozen dataclass with `bytes_, mime_type, provider, model,
    latency_ms, cost_usd`).
  - Exceptions: `ImageProviderError` and three subclasses (`RateLimited,
    Unavailable, InvalidOutput`).
  - `ImageProvider` ABC with `name`, `async health() -> bool`, `async
    generate(req) -> ImageResult`, `capabilities -> dict`.
- **FR-002**: `PollinationsProvider` implements the ABC against `image.pollinations.ai`:
  - HTTP GET with the URL pattern from SDD §4.4.
  - Streams bytes via `httpx.AsyncClient.stream`.
  - Timeout `T2I_TIMEOUT_S` (default 120 s).
  - Translates HTTP status → exceptions: 429 → `RateLimited`, 5xx / timeout →
    `Unavailable`, non-image content-type or 0 bytes → `InvalidOutput`.
- **FR-003**: `HuggingFaceProvider` implements the ABC against HF Inference API:
  - POST `/models/black-forest-labs/FLUX.1-schnell` with bearer
    `HUGGINGFACE_TOKEN`.
  - Body: `{"inputs": prompt, "parameters": {"seed", "width", "height"}}`.
  - Translates 429 → `RateLimited`, 503 (model loading) → `Unavailable`
    (router will backoff).
- **FR-004**: `FakeImageProvider` (in `app/providers/image/fake.py`) implements the
  ABC. Configurable via constructor: `responses: list[ImageResult | Exception]`,
  `latency_ms: int`. Pops responses in order. Used in tests.
- **FR-005**: `ImageProviderRouter` accepts a chain `list[ImageProvider]` and
  applies the policy from SDD §4.5.3:
  - For each provider in chain order: skip if `health()` returns False; retry
    `T2I_MAX_RETRIES` times on `Unavailable` with exponential backoff (2, 6, 18 s
    default); on `RateLimited` skip to next provider; on `InvalidOutput` skip to
    next without retry.
  - If chain exhausted: raise `ImageProviderUnavailable("All providers
    exhausted")` chained from the last seen exception.
- **FR-006**: All retry-and-backoff parameters MUST come from `settings.py`:
  `T2I_TIMEOUT_S`, `T2I_MAX_RETRIES`, `T2I_BACKOFF_SECONDS = [2, 6, 18]`.
- **FR-007**: The router emits structured log events:
  - `image_provider_attempt {provider, attempt, outcome, latency_ms}` (outcome
    ∈ `success | rate_limited | unavailable | invalid_output`).
  - `image_provider_skipped {provider, reason}` (reason: `health_false`).
  - `image_provider_failover {from, to, reason}` on first switch.
  - `image_provider_exhausted` on full failure.
- **FR-008**: `compute_r2_path(season_slug, chapter_public_id, panel_idx,
  image_result) -> str` is a pure helper:
  - Returns
    `seasons/{slug}/{chapter_uuid}/{panel_idx}-{sha256(bytes_)[:8]}.{ext}`.
  - `ext` map: `image/webp → webp`, `image/png → png`, `image/jpeg → jpg`.
- **FR-009**: A `chain_for_env(env: Literal["mvp", "dev", "v02"]) ->
  list[ImageProvider]` factory in `app/providers/image/__init__.py`:
  - `mvp` → `[PollinationsProvider, HuggingFaceProvider]`.
  - `dev` → `[FakeImageProvider]`.
  - `v02` → `[LocalComfyProvider, PollinationsProvider, HuggingFaceProvider]`
    (factory raises `NotImplementedError` until v0.2).
- **FR-010**: An import-graph test asserts that **no business module imports**
  any of: `httpx` directly against `image.pollinations.ai`, `huggingface_hub`,
  or the providers' internal SDKs. Module 008 imports only the abstraction.

### Non-Functional Requirements

- **NFR-001**: `health()` returns within 2 s on any provider.
- **NFR-002**: A successful `render` against `FakeImageProvider` returns in
  < 5 ms (lower bound for unit-test perf).
- **NFR-003**: Router overhead per failover decision < 50 ms (excluding
  backoff sleep).
- **NFR-004**: Memory: each `ImageResult` holds bytes inline; consumer is
  responsible for freeing. The abstraction does NOT cache.

### Out of Scope (for this feature)

- `LocalComfyProvider` real implementation. Reserved for v0.2.
- Image post-processing (downscale, format conversion). Consumer's job.
- Watermark / NSFW detection. Listed in SDD §8 R-9 as known risk; no
  mitigation in this module.
- Per-request cost accounting beyond `cost_usd=0` in MVP.
- Streaming progressive results (partial images). Synchronous bytes only.
- Pre-warming providers on FastAPI startup. Lazy on first call.
