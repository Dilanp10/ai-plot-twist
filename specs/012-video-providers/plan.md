# Implementation Plan: VideoProvider Abstraction

**Branch**: `012-video-providers` | **Date**: 2026-06-16 | **Spec**: [spec.md](./spec.md)
**Depends on**: `001-project-bootstrap`

## Summary

Pure infrastructure module. Defines `VideoProvider` ABC + 2 production
implementations (HF LTX-Video, Pollinations video) + `FakeVideoProvider` for
tests + `VideoProviderRouter` with typed-exception-driven fallback policy + 3
paid stubs (Kling, Runway, Luma) as `NotImplementedError` placeholders. No HTTP
endpoints, no DB writes, no FSM integration. Consumed by module 008 delta.

## Technical Context

**Languages/Versions**: Python 3.11.
**New deps**:
- `httpx ~=0.27` — already in project from module 001.
- `mutagen ~=1.47` — pure-Python MP4 metadata parser; needed for duration
  validation inside providers. Specifically `mutagen.mp4.MP4`. ~200 KB
  installed; no C extensions.
- No HF SDK, no Pollinations SDK — raw `httpx` (research R-001).

**Storage**: none.
**Testing**: `FakeVideoProvider` covers all CI tests; `@pytest.mark.live`
guards real-API tests (nightly only).
**Performance Goals**: see NFR-001..NFR-004 in spec.
**Constraints**: Gate 1 zero-cost — HF Inference API free tier, Pollinations
unauth. `mutagen` is zero-cost.
**Scale/Scope**: 4-6 `render()` calls per nightly generation, < 13 hr window.

## Constitution Check

### Gate 1 — Zero-cost
- [x] HF Inference API free tier (token required, no billing).
- [x] Pollinations video beta — no auth, no billing.
- [x] `mutagen` — MIT license, zero runtime cost.
- [x] Paid stubs (Kling/Runway/Luma) raise `NotImplementedError`; they never
      incur API cost until a real implementation replaces them.

### Gate 2 — Idempotency
- [x] `render(req)` with the same seed is idempotent at the provider level
      (same seed → same clip bytes, within provider guarantees).
- [x] No mutation; module is not state-bearing.
- [x] `compute_r2_clip_path` is pure; repeated upload of same bytes → same R2
      key → idempotent PUT.

### Gate 3 — TZ anchoring
- [x] N/A. No timestamps generated in this module.

### Gate 4 — Provider abstraction
- [x] **This module IS the abstraction.** Module 008 delta imports only from
      `app.providers.video`; it never imports individual provider sub-modules.
- [x] Import-graph test (FR-011) enforces this at CI level.

### Gate 5 — Determinism
- [x] Same `(prompt, seed, duration_s, width, height, fps)` → same clip bytes
      from providers (within provider guarantees).
- [x] `compute_r2_clip_path` is pure.
- [x] `_derive_num_frames` is pure and deterministic (contracts doc).

### Gate 6 — Spanish / English
- [x] All identifiers English.
- [x] No user-facing strings in this module (pure infrastructure).

### Gate 7 — Soft delete
- [x] N/A. No user content managed here.

### Gate 8 — Tests from day one
- [x] Unit: base types + `_derive_num_frames` edge cases + router fallback
      policy table coverage + `FakeVideoProvider` determinism +
      `compute_r2_clip_path` + stub `NotImplementedError` assertions.
- [x] Live (nightly): one HF LTX-Video request + one Pollinations request.

### Gate 9 — Trust boundaries
- [x] Provider output validated before `VideoResult` is returned: MIME must be
      `video/mp4`, bytes must be non-empty, duration must be ≥ 80% of
      requested. Untrusted by default.
- [x] No URL fragments from user input reach the provider. Module 008 composes
      prompts server-side; providers receive only a server-built `VideoRequest`.

### Gate 10 — Observability
- [x] Seven structured log events documented in spec FR-007:
      `video_provider_attempt`, `video_provider_skipped`,
      `video_provider_failover`, `video_provider_exhausted`,
      `video_provider_short_clip`.

## Project Structure

```text
specs/012-video-providers/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── contracts/
│   └── video-providers.md
├── quickstart.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

```text
apps/api/
├── app/
│   ├── providers/
│   │   ├── video/
│   │   │   ├── __init__.py            ← chain_for_env factory
│   │   │   ├── base.py                ← ABC + dataclasses + exceptions
│   │   │   ├── hf.py                  ← HFVideoProvider + _derive_num_frames
│   │   │   ├── pollinations.py        ← PollinationsVideoProvider
│   │   │   ├── fake.py                ← FakeVideoProvider + MINIMAL_MP4
│   │   │   ├── router.py              ← VideoProviderRouter
│   │   │   ├── paths.py               ← compute_r2_clip_path
│   │   │   ├── kling.py               ← KlingProvider (stub)
│   │   │   ├── runway.py              ← RunwayProvider (stub)
│   │   │   └── luma.py                ← LumaProvider (stub)
│   │   └── image/                     ← module 009 (unchanged)
│   └── core/
│       └── config.py                  ← MODIFIED (T2V_* settings added)
└── tests/
    ├── unit/
    │   ├── test_video_base.py          ← VideoRequest, VideoResult, exceptions
    │   ├── test_video_num_frames.py    ← _derive_num_frames edge cases
    │   ├── test_video_router.py        ← fallback policy table coverage
    │   ├── test_video_paths.py         ← compute_r2_clip_path
    │   ├── test_hf_video_provider.py   ← HFVideoProvider (httpx mocked)
    │   ├── test_pollinations_video_provider.py
    │   ├── test_fake_video_provider.py
    │   └── test_video_stubs.py         ← Kling/Runway/Luma raise NotImplementedError
    └── live/
        ├── test_hf_video_smoke.py      ← @pytest.mark.live
        └── test_pollinations_video_smoke.py
```

## Phase 0 — Research

See [research.md](./research.md). Key decisions:

- Raw `httpx` over any SDK (avoid dep creep).
- `mutagen` for MP4 duration validation inside providers (not ffmpeg — keeps
  providers dep-free from heavy binaries; ffmpeg belongs to module 008).
- Exception taxonomy: 3 named subclasses (`RateLimited` / `Unavailable` /
  `InvalidOutput`). Semantics documented in research R-004.
- Backoff `[5, 15, 45]` (higher than T2I's `[2, 6, 18]` — T2V cold-starts
  are longer). Research R-005.
- Pollinations video endpoint is beta/unstable — `health()` must return `False`
  gracefully if the endpoint changes, not raise. Research R-003.
- `_derive_num_frames(duration_s, fps)` formula for LTX-Video's `n*8+1`
  constraint. Research R-002 + OQ-VP-5.
- Paid stubs: `capabilities` populated, `health()` + `generate()` raise
  `NotImplementedError`. Research R-008.

## Phase 1 — Design Artefacts

- [contracts/video-providers.md](./contracts/video-providers.md) — interface contract.
- [data-model.md](./data-model.md) — no schema changes; documents R2 clip path
  contract and new `manifest_json` schema_version 2.0 shape.
- [quickstart.md](./quickstart.md).
- [checklists/requirements.md](./checklists/requirements.md).
- [tasks.md](./tasks.md).

## Phase 2 — Implementation Sequence

1. **T-001** — `app/providers/video/base.py`: `VideoRequest`, `VideoResult`,
   exception hierarchy, `VideoProvider` ABC.
2. **T-002** — `app/providers/video/fake.py`: `FakeVideoProvider` + `MINIMAL_MP4`
   constant (parseable by `mutagen`). First tests green.
3. **T-003** — `app/providers/video/hf.py`: `HFVideoProvider` + `_derive_num_frames`.
   `mutagen` duration validation. Unit tests with mocked `httpx`.
4. **T-004** — `app/providers/video/pollinations.py`: `PollinationsVideoProvider`.
   Unit tests with mocked `httpx`. `health()` returns `False` gracefully on
   endpoint unreachability.
5. **T-005** — `app/providers/video/router.py`: `VideoProviderRouter` with full
   fallback policy. Unit tests cover all 7 log events + exhausted path +
   `NotImplementedError` propagation.
6. **T-006** — `app/providers/video/paths.py`: `compute_r2_clip_path`. Unit tests
   cover idempotency + content-addressing + regex match.
7. **T-007** — `app/providers/video/__init__.py`: `chain_for_env` factory.
   `config.py` updated with `T2V_*` settings.
8. **T-008** — Paid stubs: `kling.py`, `runway.py`, `luma.py`. Unit tests assert
   each is a `VideoProvider` subclass and each method raises `NotImplementedError`.
9. **T-009** — Live smoke tests behind `@pytest.mark.live`: one real HF LTX-Video
   call + one real Pollinations video call. Asserted: `VideoResult.mime_type ==
   "video/mp4"`, `duration_s >= req.duration_s * 0.8`, bytes non-empty.
10. **T-010** — Import-graph guard test: asserts no file under `app/api/`,
    `app/domain/`, or `app/scripts/` contains the banned URL literals.

## Risks & Mitigations

| ID | Risk | Mitigation |
|---|---|---|
| **R-VP1** | Pollinations video beta endpoint URL changes or is discontinued | `PollinationsVideoProvider.health()` returns `False` on any connectivity failure → router skips it gracefully; module 008 degrades to T2I. URL centralized in one constant in `pollinations.py`. |
| **R-VP2** | HF LTX-Video cold-start (503) adds > 60 s latency on first call | Classified as `Unavailable` → backoff `[5, 15, 45]`. First warm-up call may be slow; subsequent calls hit a warm model. Nightly timing is generous (13 h window). |
| **R-VP3** | LTX-Video `num_frames % 8 != 1` rejected by HF API | `_derive_num_frames` enforces the constraint. Unit tested with boundary inputs (`duration_s=1.0`, `2.0`, `5.0`, `5.1`, `10.0`). |
| **R-VP4** | `mutagen` cannot parse a corrupt or incomplete MP4 returned by a provider | `mutagen.mp4.MP4` raises `MutagenError` → catch → re-raise as `VideoProviderInvalidOutput`. Router skips to next provider. |
| **R-VP5** | Module 008 delta sneaks a direct `httpx` call to a provider | Import-graph test (T-010) catches this at CI level. |
| **R-VP6** | Paid stub accidentally activated in `chain_for_env("paid_v1")` before implementation | Factory raises `NotImplementedError`; stubs' `health()` also raises `NotImplementedError`. Fails loudly in staging before reaching prod. |

## Post-Conditions

After merge:

- Module 008 delta imports from `app.providers.video`:
  ```python
  from app.providers.video import (
      VideoProviderRouter, VideoRequest, chain_for_env
  )
  from app.providers.video.paths import compute_r2_clip_path
  ```
- Module 008 delta instantiates `router = chain_for_env("mvp")` and renders
  each clip via `router.render(VideoRequest(...))`.
- Tests in module 008 delta use `FakeVideoProvider` to inject deterministic
  `VideoResult` bytes without network calls.
- `chain_for_env("paid_v1")` is available but raises `NotImplementedError`
  until a future paid-T2V module provides real implementations.
- Module 009 (`ImageProviderRouter`) is untouched; it remains the last-resort
  fallback invoked by module 008 when `VideoProviderUnavailable` is raised.
