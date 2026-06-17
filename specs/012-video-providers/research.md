# Phase 0 Research: VideoProvider Abstraction

**Branch**: `012-video-providers` | **Date**: 2026-06-16

---

## R-001 — HTTP client: raw `httpx` vs vendor SDKs

**Question**: do we use vendor SDKs for HF or Pollinations, or raw `httpx`?

| Option | Pros | Cons |
|---|---|---|
| **Raw `httpx` (chosen)** | Smallest dep surface; identical pattern across providers; transparent timeout/streaming control | Manual auth header; we own retry logic |
| `huggingface-hub` SDK | Idiomatic HF access | Pulls in transitive deps (filelock, fsspec, etc.); locks us to HF release cadence |
| Pollinations SDK | — | Doesn't exist |
| Kling SDK | Official Go SDK only; Python unofficial | Unofficial SDKs reverse-engineer the web app — not suitable for production |

**Decision**: **raw `httpx`** — same rationale as module 009 (R-001 there).
Both providers are plain HTTP (POST for HF, GET for Pollinations). The cost is
~40 LOC per provider. The `httpx.AsyncClient.stream()` path is used for both
because `.mp4` clips are larger than images (several MB each).

---

## R-002 — HF LTX-Video: endpoint, auth, and parameters

**Question**: what is the exact HF Inference API surface for LTX-Video?

**Endpoint** (verified as of 2026-Q2):
```
POST https://api-inference.huggingface.co/models/Lightricks/LTX-Video
Authorization: Bearer {HUGGINGFACE_TOKEN}
Content-Type: application/json
```

**Body**:
```json
{
  "inputs": "<prompt text>",
  "parameters": {
    "num_frames": 121,
    "width": 512,
    "height": 512,
    "num_inference_steps": 50,
    "guidance_scale": 3.0,
    "seed": 42
  }
}
```

**Notes**:
- `num_frames` is the primary duration knob: at 24 fps, 121 frames → ~5 s.
  LTX-Video requires `num_frames % 8 == 1` (architecture constraint). Valid
  values for MVP: 49 (≈2 s), 97 (≈4 s), 121 (≈5 s).
- `num_inference_steps=50` is the default tradeoff between quality and speed.
  Lower values (25) halve latency at some quality cost; higher (75) rarely
  worth it on the free tier.
- Seed is honored for reproducibility (same seed + same prompt → same clip).
- **Response**: binary `video/mp4` bytes in the response body. Status 200 on
  success.
- **Latency (cold)**: 120–300 s on free tier (model load + inference).
- **Latency (warm)**: 20–60 s on free tier.
- **Rate limits**: HF free tier is undocumented but empirically ~10 req/hour
  per token before 429. The token can be a read-only token (no write permissions
  needed).
- **Model cold-start**: the model can respond with 503 `{"error": "Model
  Lightricks/LTX-Video is currently loading"}` during the first ~60 s after
  inactivity. This is a transient `Unavailable`, NOT a hard failure.
- **Max resolution**: 768×768 at 24 fps for the free inference tier; larger
  sizes trigger OOM on shared GPU → 503.
- **⚠️ Verification before implementation**: endpoint paths and parameter names
  have changed between LTX-Video releases. Always `GET
  https://api-inference.huggingface.co/models/Lightricks/LTX-Video` (without
  POST body) to retrieve the current model card before coding the provider.

**Decision for MVP**: `duration_s=5`, `num_frames=121`, `width=512`,
`height=512`, `fps=24`. This gives ~5 s per clip at free-tier-safe resolution.

---

## R-003 — Pollinations Video: endpoint, auth, and parameters

**Question**: what is the Pollinations video beta endpoint?

**Endpoint** (beta, verify before implementation):
```
GET https://video.pollinations.ai/prompt/{url_encoded_prompt}?seed={seed}&width={w}&height={h}
```

**Notes**:
- No auth required (same model as their image API — community rate limits only).
- Response: `video/mp4` binary. May redirect (follow redirects).
- Latency: 30–90 s; highly variable due to shared community queue.
- Max duration: ~3-5 s. No `duration` parameter exposed in beta.
- Resolution: 512×512 or 256×256 reliably; higher resolutions may produce
  corrupted output (treat as `InvalidOutput`).
- **Rate limits**: aggressive — community queue backs up and returns HTTP 503
  when overloaded. Not suitable as primary for production load, hence fallback
  position.
- **Stability**: this is an unofficial beta endpoint. The path and parameters
  have changed 3× between 2025-Q4 and 2026-Q2. **Mandatory**: verify the
  current endpoint at `https://pollinations.ai` before implementation. If the
  video beta has been discontinued, `PollinationsVideoProvider.health()` must
  return `False` immediately (not raise), causing the router to skip it
  gracefully.
- **Seed support**: present in query params but not guaranteed deterministic
  (community GPU assignment may vary).

**Decision for MVP**: use as fallback only. If the beta endpoint changes or
disappears between implementation and production, the router degrades to
"chain exhausted" and module 008 falls back to T2I — no crash, no data loss.

---

## R-004 — Exception taxonomy

**Question**: which exceptions does `generate()` raise and what does each mean
for retry semantics?

| Exception | Trigger | Router behavior |
|---|---|---|
| `VideoProviderRateLimited` | HTTP 429; community queue full signal | **Skip** to next provider immediately |
| `VideoProviderUnavailable` | HTTP 5xx; network timeout; model cold-start 503 | **Retry** with exponential backoff (FR-005) |
| `VideoProviderInvalidOutput` | Non-video MIME; 0 bytes; corrupted MP4; clip duration < 80% of requested | **Skip** to next provider; **no retry** |
| `VideoProviderError` (base) | Generic; should not be raised directly | Propagated as-is if raised |
| `NotImplementedError` | Stub provider called | Propagated immediately — misconfigured chain |

**Rationale**: same taxonomy as module 009's image providers. Decouples policy
(router) from mechanism (provider). Each provider raises the most specific
exception; the router applies uniform policy.

---

## R-005 — Backoff parameters and total bound

**Question**: how aggressive is the retry, given T2V is much slower than T2I?

**Decision**: `T2V_BACKOFF_SECONDS = [5, 15, 45]`. Three retries per provider
on `Unavailable`. Total wait per provider in worst case:
`5 + 15 + 45 = 65 s` of sleep + 3 × `T2V_TIMEOUT_S` (300 s) = ~15 min.
With 2 providers in chain and 5 clips: ~150 min theoretical worst case.

**Why this doesn't blow the pipeline deadline**: module 008's
`PIPELINE_HARD_DEADLINE_S = 3300 s` (55 min). Module 008 generates clips
concurrently (`asyncio.gather`), so 5 clips share the deadline, not stack it.
In the absolute worst case (all clips hit all retries on both providers),
module 008 cancels ongoing renders after the deadline and falls back to T2I.
The router itself is unaware of the deadline — it's the consumer's
responsibility to cancel the coroutine.

**Configurable via env**: `T2V_BACKOFF_SECONDS_CSV="5,15,45"`.

**Comparison with T2I (module 009)**: T2I uses `[2, 6, 18]`. T2V values are
~2.5× higher because T2V cold-start is longer (model is larger, output is
bigger). Still within Gate 1 (no paid tier needed to stay within the budget).

---

## R-006 — MP4 duration validation without heavy dependencies

**Question**: how does `HFVideoProvider` (or `PollinationsVideoProvider`)
parse the actual duration from returned `.mp4` bytes to validate against
the 80% threshold from FR-002?

**Options considered**:

| Option | Pros | Cons |
|---|---|---|
| **`mutagen` (chosen)** | Pure Python; lightweight; reads MP4 `moov/mvhd` box | Another dep (but tiny: ~200 KB) |
| `av` (PyAV) | Accurate; already needed by module 008 for stitch | Heavy dep (~50 MB); not appropriate for a pure-infra provider |
| `ffprobe` subprocess | Accurate | Shell dependency inside a library; not idiomatic |
| Parse `moov/mvhd` manually | Zero deps | Brittle; different box orders break it |

**Decision**: **`mutagen`** in the provider. It can parse MP4 duration from
the `moov/mvhd` box without decoding frames:

```python
from io import BytesIO
from mutagen.mp4 import MP4

def _parse_duration(mp4_bytes: bytes) -> float:
    tags = MP4(BytesIO(mp4_bytes))
    return tags.info.length  # seconds, float
```

Module 008 will use `ffmpeg` (via `subprocess` / `ffmpeg-python`) for actual
clip stitching and audio mixing. The provider layer does NOT depend on ffmpeg.
This keeps the dependency graph clean: `app/providers/video` → `mutagen`
only; `app/pipeline` → `ffmpeg` binary + `ffmpeg-python`.

**Edge case — streaming vs full bytes**: `mutagen.MP4` requires seekable input.
If the provider streams the response, it must accumulate bytes first, then
parse. This is acceptable because MP4 clips at 5 s / 512×512 / 24 fps are
roughly 1-5 MB — safe to buffer in memory on Fly's 256 MB machine.

---

## R-007 — `compute_r2_clip_path` location and scheme

**Question**: where does the path-derivation helper live?

**Decision**: `app/providers/video/paths.py` — same pattern as module 009's
`paths.py`. The path embeds the content hash of the clip bytes, which is the
provider's output. Keeping it in the provider package preserves clean
dependency direction (008 imports from 012, not vice versa).

**Path scheme**:
```
seasons/{season_slug}/{chapter_public_id}/clips/{clip_idx}-{sha256(bytes_)[:8]}.mp4
```

- `clips/` subdirectory separates clip assets from the final stitched mp4
  (uploaded by module 008 at `seasons/{slug}/{uuid}/chapter.mp4`).
- Content-addressed: R2 PUT is idempotent, retry-safe.
- Human-readable: `clip_idx` makes it easy to identify broken clips in logs.

---

## R-008 — Paid provider stubs strategy

**Question**: how do we reserve `KlingProvider`, `RunwayProvider`, `LumaProvider`
without writing dead code?

**Decision**: same pattern as `LocalComfyProvider` in module 009 (R-005 there).
Each stub:

```python
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
            "Implementar cuando exista plan paid-T2V."
        )

    async def generate(self, req: VideoRequest) -> VideoResult:
        raise NotImplementedError(
            "KlingProvider stub — ver SDD Ronda 6 #26."
        )
```

**Why `capabilities` is populated even in stubs**: it allows tooling/tests to
introspect what the provider *will* support (resolution, FPS) for API
compatibility checks, without calling `generate()`.

**Why `health()` raises `NotImplementedError` instead of returning `False`**:
returning `False` would make the router silently skip it, masking a
misconfigured chain. `NotImplementedError` propagates loudly, revealing the
bug immediately in staging.

---

## R-009 — Concurrent clip renders

**Question**: module 008 may render 4-6 clips concurrently via `asyncio.gather`.
Does the router support this?

**Decision**: **yes, trivially** — same reasoning as module 009 R-007. The
router is stateless per call; each `render()` creates its own attempt cursor.

**Self-DoS caveat**: 5 concurrent calls to HF may hit the rate limit
simultaneously, all falling over to Pollinations simultaneously. Acceptable for
MVP (5 clips max per chapter). A per-provider semaphore can be added later
without changing the public API (implementation detail of the router).

**Trigger to revisit**: chapter clip count grows beyond 8, or rate-limiting
cascades appear in prod logs more than once per chapter.

---

## R-010 — Testing strategy: `FakeVideoProvider` invariants

**Question**: what does `FakeVideoProvider` need to do well to be useful?

**Decision**: same pattern as module 009's `FakeImageProvider` (R-008 there).
Minimum surface:

```python
class FakeVideoProvider(VideoProvider):
    name = "fake"

    def __init__(
        self,
        responses: list[VideoResult | type[Exception] | Exception],
        latency_ms: int = 0,
        health_returns: bool = True,
    ): ...

    async def health(self) -> bool:
        return self.health_returns

    async def generate(self, req: VideoRequest) -> VideoResult:
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000)
        item = self._pop_response()
        if isinstance(item, type) and issubclass(item, Exception):
            raise item()
        if isinstance(item, Exception):
            raise item
        return item
```

**Default `VideoResult`** for tests: a minimal valid MP4 (4-byte ftyp box
sufficient for duration-parsing tests; longer for integration tests).
A `MINIMAL_MP4` constant lives in `fake.py`.

**Duration parsing in tests**: `FakeVideoProvider` bypasses `mutagen` — it
returns a `VideoResult` with `duration_s` pre-populated. Tests that exercise
duration validation do so via a `FakeVideoProvider` configured to return a
`VideoResult` with short `duration_s`, or to raise `InvalidOutput` directly.

---

## R-011 — HF auth: token scope and CI strategy

**Question**: same question as module 009 R-009, but for T2V.

**Decision**: identical to module 009. Each developer creates a free HF account,
generates a read-only Inference token, sets `HUGGINGFACE_TOKEN` in
`.env.local` / Fly secrets. The same token works for both `HFVideoProvider`
(module 012) and `HFImageProvider` (module 009) — HF doesn't require separate
tokens per model.

For CI: live T2V tests are gated behind `@pytest.mark.live` and run only in
the nightly `live-llm-smoke.yml` workflow with the org-level token.

**⚠️ Cost note**: HF free Inference API for video models may consume GPU credits
on your free HF account (not monetary cost, but usage quota). Avoid running
`@pytest.mark.live` locally in tight loops. The `FakeVideoProvider` is
sufficient for all unit and integration tests that don't need real mp4 output.

---

## Open Items

- **OQ-VP-1**: per-provider concurrency semaphore (R-009 trigger — revisit
  when clip count > 8 or rate-limit cascades observed in prod).
- **OQ-VP-2**: Pollinations video beta endpoint stability — if it disappears
  between now and implementation, consider `CogVideoX-5B` on HF as the second
  free-tier provider instead (`POST /models/THUDM/CogVideoX-5B`; slower but
  more stable).
- **OQ-VP-3**: cost tracking for paid providers — `cost_usd` is already in
  `VideoResult` for forward compatibility with Kling/Runway/Luma.
- **OQ-VP-4**: minimum viable MP4 constant for `FakeVideoProvider` — confirm
  `mutagen` can parse it; write a dedicated unit test for this before the fake
  is used in module 008 tests.
- **OQ-VP-5**: LTX-Video `num_frames % 8 == 1` constraint — `HFVideoProvider`
  must derive `num_frames` from `req.duration_s` and `req.fps` and round to
  the nearest valid value. Document the rounding formula in the implementation
  (e.g., `num_frames = max(1, round(duration_s * fps / 8)) * 8 + 1`).
