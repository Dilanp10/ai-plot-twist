# Task Breakdown: VideoProvider Abstraction

**Branch**: `012-video-providers` | **Date**: 2026-06-16

---

## Phase 0 — Base types (1 PR)

### T-001 — Base ABC + dataclasses + exceptions → `001-merged`

**Files**:
- `apps/api/app/providers/__init__.py` (extend; may already exist from 006/009)
- `apps/api/app/providers/video/__init__.py` (empty package marker)
- `apps/api/app/providers/video/base.py`
- `apps/api/tests/unit/test_video_base.py`

**Body**: as in [contracts/video-providers.md](./contracts/video-providers.md).
Defines `VideoRequest`, `VideoResult`, `VideoProviderError` + 3 subclasses,
`VideoProvider` ABC.

**Test coverage**:
- `VideoRequest` is frozen (mutation raises `FrozenInstanceError`).
- `VideoResult` is frozen.
- Exception hierarchy: `VideoProviderRateLimited` is a `VideoProviderError`.
- ABC cannot be instantiated directly.

---

## Phase 1 — Implementations (4 PRs; T-003 and T-004 parallel)

### T-002 — `FakeVideoProvider` → T-001

**Files**:
- `apps/api/app/providers/video/fake.py`
- `apps/api/tests/unit/test_fake_video_provider.py`

**Constants**: `MINIMAL_MP4` — smallest valid MP4 bytes that `mutagen.mp4.MP4`
can parse to yield `info.length > 0`. Generate once with ffmpeg during
development, commit the raw bytes as a constant.

**Test coverage**:
- Pops responses in order; exhaustion raises `VideoProviderUnavailable`.
- Injected exception class is raised (not returned).
- Injected exception instance is raised.
- `latency_ms` introduces sleep.
- `health_returns=False` works.
- `MINIMAL_MP4` parses with `mutagen.mp4.MP4` without error.

### T-003 — `HFVideoProvider` → T-001 [P with T-004]

**Files**:
- `apps/api/app/providers/video/hf.py`
- `apps/api/tests/unit/test_hf_video_provider.py`

**Behavior**:
- `health()`: HTTP GET to HF Inference API with 2 s timeout; returns `True`
  if status < 500; catches all exceptions and returns `False`.
- `generate(req)`:
  1. Derive `num_frames = _derive_num_frames(req.duration_s, req.fps)`.
  2. POST `https://api-inference.huggingface.co/models/Lightricks/LTX-Video`
     with `Authorization: Bearer {HUGGINGFACE_TOKEN}`, body:
     `{"inputs": req.prompt, "parameters": {"seed": req.seed,
     "num_frames": num_frames, "width": req.width, "height": req.height,
     "num_inference_steps": 50, "guidance_scale": 3.0}}`.
  3. Stream response bytes via `httpx.AsyncClient.stream`.
  4. Verify `Content-Type` starts with `video/`; raise `InvalidOutput` otherwise.
  5. Parse duration with `mutagen.mp4.MP4(BytesIO(bytes_)).info.length`.
  6. Validate duration ≥ `req.duration_s * 0.8`; raise `InvalidOutput` if short.
  7. Return `VideoResult`.
- Status translations: 429 → `RateLimited`; 503 containing `"estimated_time"`
  or `"currently loading"` → `Unavailable` (cold start); any other 5xx →
  `Unavailable`; timeout → `Unavailable`; `MutagenError` → `InvalidOutput`.

**`_derive_num_frames` formula** (internal helper, unit-tested separately in
`test_video_num_frames.py`):
```python
def _derive_num_frames(duration_s: float, fps: int) -> int:
    raw = round(duration_s * fps)
    n = max(1, round((raw - 1) / 8))
    return n * 8 + 1
```

**Test coverage** (all with mocked `httpx`):
- Happy path: 200 + valid mp4 bytes → `VideoResult`.
- 429 → `VideoProviderRateLimited`.
- 503 cold-start → `VideoProviderUnavailable`.
- 500 → `VideoProviderUnavailable`.
- Timeout → `VideoProviderUnavailable`.
- Non-video content-type → `VideoProviderInvalidOutput`.
- Duration < 80% of requested → `VideoProviderInvalidOutput`.
- Duration in 80-100% range → accepted (with warning emitted).
- `_derive_num_frames` edge cases (see checklist).

### T-004 — `PollinationsVideoProvider` → T-001 [P with T-003]

**Files**:
- `apps/api/app/providers/video/pollinations.py`
- `apps/api/tests/unit/test_pollinations_video_provider.py`

**Behavior**:
- `health()`: HTTP GET to Pollinations video endpoint root with 2 s timeout;
  returns `False` on ANY failure (connectivity, 4xx, 5xx) — never raises.
- `generate(req)`: GET `https://video.pollinations.ai/prompt/{encoded_prompt}
  ?seed={req.seed}&width={req.width}&height={req.height}` with follow-redirects;
  stream response; same content-type + duration validation as T-003.
- **⚠️ Before coding**: verify the current Pollinations video endpoint URL at
  `https://pollinations.ai`. If it has changed, update the constant and note
  the new URL in a code comment with the date verified.

**Test coverage** (all with mocked `httpx`):
- Happy path.
- 429 → `RateLimited`.
- 503 → `Unavailable`.
- Timeout → `Unavailable`.
- Non-video content-type → `InvalidOutput`.
- Duration < 80% → `InvalidOutput`.
- `health()` returns `False` on connection error (does NOT raise).
- Prompt is URL-encoded in the GET path.

---

## Phase 2 — Router (1 PR)

### T-005 — `VideoProviderRouter` → T-002

**Files**:
- `apps/api/app/providers/video/router.py`
- `apps/api/tests/unit/test_video_router.py`

**Tests** (all nine branches from FR-005, all using `FakeVideoProvider`):

- `test_success_on_first_provider`
- `test_rate_limited_skips_to_next`
- `test_unavailable_retries_then_succeeds` (mock `asyncio.sleep` via
  `monkeypatch.setattr("asyncio.sleep", AsyncMock())`)
- `test_unavailable_all_providers_exhausted` (chain exception is chained
  from last seen via `raise … from last_exc`)
- `test_invalid_output_skips_no_retry`
- `test_health_false_skips_no_attempt`
- `test_short_clip_within_tolerance_accepted` (80% boundary)
- `test_short_clip_below_tolerance_invalid_output`
- `test_stub_provider_not_implemented_propagates`

**Structured log assertions**: use `pytest-structlog` or `caplog` to assert
each of the 7 events from FR-007 fires on the correct branch.

---

## Phase 3 — Helpers + factory + stubs (3 PRs; T-006 parallel with T-007/T-008)

### T-006 — `compute_r2_clip_path` → T-001 [P with T-007, T-008]

**Files**:
- `apps/api/app/providers/video/paths.py`
- `apps/api/tests/unit/test_video_paths.py`

**Test coverage**:
- Same `(slug, uuid, idx, result)` → same path (idempotency, called twice).
- Different bytes → different hash → different path.
- Path matches regex `^seasons/[a-z0-9-]+/[0-9a-f-]{36}/clips/\d+-[0-9a-f]{8}\.mp4$`.
- `clip_idx=0` renders as `0` (not `1`-indexed).

### T-007 — `chain_for_env` factory → T-002, T-003, T-004

**Files**:
- `apps/api/app/providers/video/__init__.py` (extend with `chain_for_env`)
- `apps/api/app/core/config.py` (add `T2V_TIMEOUT_S`, `T2V_MAX_RETRIES`,
  `T2V_BACKOFF_SECONDS`)
- `.env.example` (extend with T2V vars)
- `apps/api/tests/unit/test_video_chain_for_env.py`

**Test coverage**:
- `"mvp"` → list of 2 providers with correct types.
- `"dev"` → list of 1 `FakeVideoProvider`.
- `"paid_v1"` → raises `NotImplementedError`.
- Settings read from env: override `T2V_MAX_RETRIES=1` via monkeypatch → router
  created by factory uses value 1.

### T-008 — Paid stubs → T-001

**Files**:
- `apps/api/app/providers/video/kling.py`
- `apps/api/app/providers/video/runway.py`
- `apps/api/app/providers/video/luma.py`
- `apps/api/tests/unit/test_video_stubs.py`
- `docs/adr/0006-paid-video-providers.md` (skeleton referencing SDD Ronda 6 #26)

**Body**: as in [contracts/video-providers.md](./contracts/video-providers.md).
Each stub: `capabilities` populated, `health()` + `generate()` raise
`NotImplementedError`.

**Test coverage** (parametrized across the 3 stubs):
- Each is importable.
- Each is a subclass of `VideoProvider`.
- `capabilities` returns a dict with keys `max_duration_s`,
  `supported_resolutions`, `supported_fps` without raising.
- `health()` raises `NotImplementedError`.
- `generate()` raises `NotImplementedError`.

---

## Phase 4 — Guard + live (2 PRs)

### T-009 — Live smoke tests → T-003, T-004

**Files**:
- `apps/api/tests/live/test_hf_video_smoke.py`
- `apps/api/tests/live/test_pollinations_video_smoke.py`
- `.github/workflows/live-llm-smoke.yml` (extend from module 006)

**Behavior**: tagged `@pytest.mark.live`; skipped on PR CI. Each test:
1. Renders one `VideoRequest(prompt="…", seed=42, duration_s=5.0, …)`.
2. Asserts `result.mime_type == "video/mp4"`.
3. Asserts `result.duration_s >= 5.0 * 0.8`.
4. Asserts `len(result.bytes_) > 0`.
5. Writes to `/tmp/smoke_hf.mp4` or `/tmp/smoke_pollinations.mp4`.
6. Asserts `mutagen.mp4.MP4(BytesIO(result.bytes_)).info.length > 0`.

### T-010 — Import-graph guard test → T-001..T-008

**Files**:
- `apps/api/tests/unit/test_video_import_graph.py`

**Implementation**: walks `app/api/`, `app/domain/`, `app/scripts/`; for each
`.py` file asserts:
- `"video.pollinations.ai"` not in file content.
- `"api-inference.huggingface.co/models/Lightricks"` not in file content.
- No `from app.providers.video.hf import` outside `app/providers/video/`.
- No `from app.providers.video.pollinations import` outside `app/providers/video/`.
- No `from app.providers.video.kling import` outside `app/providers/video/`.

---

## Done-when (module-level acceptance)

1. All 10 tasks merged to `main`.
2. Every box in [checklists/requirements.md](./checklists/requirements.md)
   ticked.
3. Live tests pass against both providers (manual run or nightly CI).
4. Import-graph guard test green.
5. `specs/README.md` marks module 012 `done`.
6. Module 008 delta can import from `app.providers.video` without circular
   dependencies.

---

## Estimates (solo dev, calendar days)

| Phase | Tasks | Est. days |
|---|---|---|
| 0 — Base | T-001 | 0.5 |
| 1 — Implementations | T-002..T-004 | 3.0 |
| 2 — Router | T-005 | 1.5 |
| 3 — Helpers + factory + stubs | T-006..T-008 | 1.5 |
| 4 — Guard + live | T-009..T-010 | 1.0 |
| **Total** | 10 tasks | **≈ 7.5 days** |

Buffer +20% → **plan for 9 working days**.

> T-003 is the heaviest task (1.5 d) due to `mutagen` integration + 9 mocked
> error paths + `_derive_num_frames` edge cases. If it runs long, T-004 can
> start in parallel once T-001 is merged.
