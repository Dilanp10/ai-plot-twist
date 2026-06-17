# Feature Specification: VideoProvider Abstraction

**Feature Branch**: `012-video-providers`
**Created**: 2026-06-16
**Status**: Draft
**Depends on**: `001-project-bootstrap`

## Summary

Ship the `VideoProvider` abstraction aligned with SDD Ronda 6 (#22-27): an
abstract base class with typed exceptions, two production implementations
(`HFVideoProvider` for LTX-Video, `PollinationsVideoProvider`), a fallback
`VideoProviderRouter` with chain semantics, a `FakeVideoProvider` for tests,
and three paid stubs (`KlingProvider`, `RunwayProvider`, `LumaProvider`) that
raise `NotImplementedError` until a paid-T2V module is developed.

The router produces **raw video clips** (`.mp4` bytes per clip). Stitching
N clips with ffmpeg, mixing edge-tts narration, and degrading to T2I (module
009) if the full T2V chain fails are all responsibilities of module 008
(generation-pipeline). This boundary keeps the abstraction type-clean: the
router always returns `VideoResult` (or raises), never an image fallback.

No HTTP endpoints. No DB writes. No FSM integration. This module is **pure
infrastructure** that module 008 imports.

The paid providers — `KlingProvider`, `RunwayProvider`, `LumaProvider` — are
**NOT functional in this module**. They are reserved for a future paid-T2V
module (tentatively `013-paid-video-providers`). The stubs exist so the router
chain can reference them by type, enabling a zero-diff swap when budget allows.

## User Scenarios & Testing

### User Story 1 — A consumer renders a video clip via the router (Priority: P1)

Module 008 imports `VideoProviderRouter`, calls `router.render(VideoRequest)`,
gets back `VideoResult` with `.mp4` bytes. HF LTX-Video succeeds on first try
in the common case.

**Why this priority**: this is the entire purpose of the module. Module 008
cannot generate chapter clips without it.

**Independent Test**: unit test with `FakeVideoProvider` returning a minimal
valid `.mp4`; integration test with real HF Inference API behind
`@pytest.mark.live`.

**Acceptance Scenarios**:

1. **Given** a router with chain `[HFVideoProvider, PollinationsVideoProvider]`
   and both healthy,
   **When** the consumer calls `router.render(VideoRequest(prompt="…", seed=42,
   duration_s=5, width=512, height=512, fps=24, aspect="9:16", style_tag=None))`,
   **Then** HFVideoProvider is tried first, returns `VideoResult(bytes_,
   "video/mp4", "hf", "ltx-video", duration_s=5.0, frames_count=120,
   latency_ms=…, cost_usd=0)`, the router emits a
   `video_provider_attempt {provider:"hf", attempt:0, outcome:"success"}`
   log.

2. **Given** the request includes a seed,
   **When** the same request is rendered twice (on the same provider with seed
   support),
   **Then** the provider returns a deterministic clip (byte-identical or
   perceptually identical per provider guarantee).

### User Story 2 — Failover from HF to Pollinations (Priority: P1)

HF returns 429 (rate limited). The router skips to Pollinations without
exhausting retries on the dead provider.

**Acceptance Scenarios**:

1. **Given** `HFVideoProvider` is mocked to raise `VideoProviderRateLimited`,
   **When** the router renders,
   **Then** HF is tried once; on `RateLimited`, the router skips directly to
   Pollinations. Logs include
   `video_provider_attempt {provider:"hf", outcome:"rate_limited"}` followed by
   `video_provider_attempt {provider:"pollinations", attempt:0, outcome:"success"}`.

2. **Given** `HFVideoProvider` raises `VideoProviderUnavailable` on every
   attempt,
   **When** the router renders,
   **Then** HF is tried `T2V_MAX_RETRIES` times with exponential backoff
   (5 s, 15 s, 45 s); then Pollinations is tried. Total time bounded by
   ≤ `(5 + 15 + 45) + Pollinations_first_attempt_latency`.

3. **Given** both providers are unavailable through all retries,
   **When** the router renders,
   **Then** it raises `VideoProviderUnavailable("All providers exhausted")` with
   the last exception chained (`raise … from`). No partial result; no panic.
   Module 008 catches this and falls back to the T2I pipeline (module 009).

### User Story 3 — Health gate skips dead providers without consuming retries (Priority: P2)

If a provider's `health()` returns False, the router doesn't even attempt a
render against it.

**Acceptance Scenarios**:

1. **Given** HFVideoProvider `.health()` returns False,
   **When** the router renders,
   **Then** HF is skipped (no `generate` call), router moves to Pollinations.
   Log: `video_provider_skipped {provider:"hf", reason:"health_false"}`.

### User Story 4 — `InvalidOutput` fails fast without retry (Priority: P2)

The provider returns a payload that doesn't pass `VideoResult` validation (e.g.,
non-video MIME, 0-byte response, actual duration < 80% of requested duration).
The router treats this as a hard fail for that provider — no retry, immediate
skip to the next.

**Why this priority**: retrying on `InvalidOutput` is usually pointless (the
provider's bug, not transient), and the spec must be unambiguous about this
boundary.

**Acceptance Scenarios**:

1. **Given** HFVideoProvider returns a 200 with `Content-Type: text/html` (CDN
   error page) and the provider's parser raises `VideoProviderInvalidOutput`,
   **When** the router renders,
   **Then** HF is attempted **once** (attempt 0), no retries, router moves to
   Pollinations.

2. **Given** HFVideoProvider returns a valid `.mp4` with `duration_s=1.0` when
   `VideoRequest.duration_s=5.0` (< 80% threshold),
   **When** the router renders,
   **Then** the provider raises `VideoProviderInvalidOutput("duration too short:
   got 1.0s, expected >= 4.0s")`, router skips to Pollinations.

3. **Given** HFVideoProvider returns a valid `.mp4` with `duration_s=4.1` when
   `VideoRequest.duration_s=5.0` (≥ 80% threshold),
   **When** the router renders,
   **Then** the result is accepted. Log warns:
   `video_provider_short_clip {provider:"hf", requested_s:5.0, actual_s:4.1}`.

### User Story 5 — Asset path derivation for individual clips (Priority: P2)

Module 008 uploads each clip to R2 before stitching. The helper computes a
deterministic, content-addressed path.

**Acceptance Scenarios**:

1. **Given** a `VideoResult` with `bytes_`,
   **When** the consumer calls `compute_r2_clip_path(season_slug,
   chapter_public_id, clip_idx, video_result)`,
   **Then** the returned path is
   `seasons/{season_slug}/{chapter_public_id}/clips/{clip_idx}-{sha256(bytes_)[:8]}.mp4`.
   Same input → same path (content-addressed).

### Edge Cases

- **Provider returns 0-byte response**: parser raises `InvalidOutput`.
- **Provider returns non-video content-type**: parser raises `InvalidOutput`.
- **Clip duration < 80% of `req.duration_s`**: provider raises `InvalidOutput`
  (see US4 scenario 2).
- **Clip duration 80-100% of `req.duration_s`**: accepted with warning log
  (see US4 scenario 3).
- **Provider returns > requested resolution**: accepted as-is. Consumer (008)
  re-encodes during stitch if needed.
- **Network timeout > `T2V_TIMEOUT_S`**: provider raises
  `VideoProviderUnavailable("timeout")`; router retries with backoff.
- **All providers exhausted before `PIPELINE_HARD_DEADLINE_S`**: module 008
  catches `VideoProviderUnavailable` and falls back to T2I. The router does
  not know about deadlines.
- **Router `T2V_MAX_RETRIES × backoff` total > `PIPELINE_HARD_DEADLINE_S`**:
  module 008 is responsible for cancelling the router call before the hard
  deadline. The router itself has no timeout budget awareness.
- **Concurrent calls to the same router**: each call has its own attempt cursor;
  no shared mutable state. Tested with `asyncio.gather`.
- **Stub provider in chain**: if `KlingProvider` is included in a chain at
  runtime, its `generate()` raises `NotImplementedError` — the router treats
  this as an unrecoverable error and propagates it (not as `Unavailable`), so
  misconfigured chains fail loudly.

## Requirements

### Functional Requirements

- **FR-001**: `app/providers/video/base.py` defines:
  - `VideoRequest` — frozen dataclass with fields:
    `prompt: str`, `seed: int`, `duration_s: float`, `width: int`,
    `height: int`, `fps: int`, `aspect: str`, `style_tag: str | None`.
  - `VideoResult` — frozen dataclass with fields:
    `bytes_: bytes`, `mime_type: str`, `provider: str`, `model: str`,
    `duration_s: float`, `frames_count: int`, `latency_ms: int`,
    `cost_usd: float`.
  - Exception hierarchy:
    `VideoProviderError(Exception)` base, and three subclasses:
    `VideoProviderRateLimited`, `VideoProviderUnavailable`,
    `VideoProviderInvalidOutput`.
  - `VideoProvider` ABC with:
    - `name: str` (abstract property)
    - `async health() -> bool`
    - `async generate(req: VideoRequest) -> VideoResult`
    - `capabilities: dict` — must include keys `max_duration_s`,
      `supported_resolutions: list[tuple[int,int]]`, `supported_fps: list[int]`.

- **FR-002**: `app/providers/video/hf.py` — `HFVideoProvider` implements the
  ABC against HF Inference API:
  - POST to `https://api-inference.huggingface.co/models/Lightricks/LTX-Video`
    with `Authorization: Bearer {HUGGINGFACE_TOKEN}`.
  - Body: `{"inputs": prompt, "parameters": {"seed", "num_frames": fps×duration_s,
    "width", "height"}}`.
  - Timeout `T2V_TIMEOUT_S` (default 300 s).
  - Response: binary `.mp4` bytes.
  - Duration validation: parse actual duration from mp4 metadata; raise
    `InvalidOutput` if < 80% of requested.
  - HTTP status translations: 429 → `RateLimited`; 503 (model loading) →
    `Unavailable`; 5xx / timeout → `Unavailable`; non-`video/mp4` content-type
    or 0 bytes → `InvalidOutput`.
  - `capabilities.max_duration_s = 5`.

- **FR-003**: `app/providers/video/pollinations.py` — `PollinationsVideoProvider`
  implements the ABC against Pollinations video beta:
  - GET `https://video.pollinations.ai/prompt/{encoded_prompt}` (or equivalent
    current endpoint — verify in `research.md` before implementation).
  - No auth required.
  - Timeout `T2V_TIMEOUT_S`.
  - Same status translations and duration validation as FR-002.
  - `capabilities.max_duration_s = 5`.

- **FR-004**: `app/providers/video/fake.py` — `FakeVideoProvider` implements
  the ABC:
  - Constructor: `responses: list[VideoResult | Exception]`, `latency_ms: int = 0`.
  - `generate()` pops responses in order; if list exhausted raises
    `VideoProviderUnavailable("FakeVideoProvider exhausted")`.
  - `health()` always returns `True` unless overridden via constructor flag
    `healthy: bool = True`.
  - Used exclusively in tests; never imported by production code.

- **FR-005**: `app/providers/video/router.py` — `VideoProviderRouter` accepts
  `chain: list[VideoProvider]` and applies the following policy on each
  `render(req: VideoRequest)` call:
  - For each provider in chain order:
    1. Call `health()`; if `False` → log `video_provider_skipped`, continue.
    2. Attempt `generate(req)` up to `T2V_MAX_RETRIES` times:
       - `RateLimited` → log attempt, break inner loop, continue to next provider.
       - `Unavailable` → log attempt, exponential backoff (`T2V_BACKOFF_SECONDS[i]`),
         retry if attempts remain; else continue to next provider.
       - `InvalidOutput` → log attempt, break inner loop (no retry), continue
         to next provider.
       - `NotImplementedError` → propagate immediately (misconfigured chain).
       - Success → log `video_provider_attempt {outcome:"success"}`, return result.
  - If chain exhausted → raise
    `VideoProviderUnavailable("All providers exhausted")` chained from last
    exception.

- **FR-006**: All retry/timeout parameters come from `settings.py`:
  - `T2V_TIMEOUT_S: int = 300`
  - `T2V_MAX_RETRIES: int = 3`
  - `T2V_BACKOFF_SECONDS: list[int] = [5, 15, 45]`

- **FR-007**: The router emits structured log events via `structlog`:
  - `video_provider_attempt {provider, attempt, outcome, latency_ms}` —
    outcome ∈ `success | rate_limited | unavailable | invalid_output`.
  - `video_provider_skipped {provider, reason}` — reason: `health_false`.
  - `video_provider_failover {from_, to, reason}` on first switch between
    providers.
  - `video_provider_exhausted {chain, last_exception}` on full failure.
  - `video_provider_short_clip {provider, requested_s, actual_s}` when clip
    is within 80-100% tolerance (accepted with warning).

- **FR-008**: `compute_r2_clip_path(season_slug: str, chapter_public_id: str,
  clip_idx: int, video_result: VideoResult) -> str` — pure helper:
  - Returns
    `seasons/{season_slug}/{chapter_public_id}/clips/{clip_idx}-{sha256(bytes_)[:8]}.mp4`.
  - Always `.mp4` extension (only MIME accepted in MVP).
  - Same input → same path (deterministic, content-addressed).

- **FR-009**: `app/providers/video/__init__.py` exposes `chain_for_env`:
  ```python
  def chain_for_env(env: Literal["mvp", "dev", "paid_v1"]) -> list[VideoProvider]:
  ```
  - `"mvp"` → `[HFVideoProvider(), PollinationsVideoProvider()]`
  - `"dev"` → `[FakeVideoProvider()]`
  - `"paid_v1"` → `[KlingProvider(), RunwayProvider(), LumaProvider()]`
    — raises `NotImplementedError` until paid module ships.

- **FR-010**: Paid stubs in `app/providers/video/`:
  - `kling.py` → `KlingProvider`
  - `runway.py` → `RunwayProvider`
  - `luma.py` → `LumaProvider`
  - Each implements `VideoProvider` ABC. All methods raise:
    `raise NotImplementedError(f"{self.__class__.__name__} stub — ver SDD Ronda 6 #26")`.
  - A dedicated test asserts each stub is importable, is a subclass of
    `VideoProvider`, and that `generate()` raises `NotImplementedError`
    (not `AttributeError`).

- **FR-011**: Import-graph test asserts that no business module (e.g., module
  008) imports `httpx` directly against HF or Pollinations video endpoints, nor
  imports provider internals. Module 008 imports only from
  `app.providers.video` public surface.

### Non-Functional Requirements

- **NFR-001**: `health()` returns within 2 s on any provider.
- **NFR-002**: `FakeVideoProvider.generate()` returns in < 5 ms.
- **NFR-003**: Router overhead per failover decision < 50 ms (excluding backoff
  sleep and actual provider latency).
- **NFR-004**: Memory: `VideoResult.bytes_` is held inline; no internal cache
  in the router. Consumer is responsible for streaming to R2 and releasing.

### Out of Scope (for this feature)

- **ffmpeg stitching** of N clips into a single video. Module 008's job.
- **edge-tts narration mixing** over the stitched video. Module 008's job.
- **Degradation to T2I** when all T2V providers fail. Module 008's job
  (catches `VideoProviderUnavailable` and calls `ImageProviderRouter`).
- **Real implementation of Kling / Runway / Luma**. Reserved for a future
  `013-paid-video-providers` module.
- **Watermark / NSFW detection** on video frames.
- **Streaming progressive output** (partial frames while generating).
- **Per-request cost accounting** beyond `cost_usd=0.0` in MVP.
- **Pre-warming providers** on FastAPI startup.
- **Audio track extraction** from provider-returned clips (Pollinations may
  include silence; that's acceptable — 008 mixes its own audio).
