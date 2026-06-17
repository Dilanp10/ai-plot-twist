# Requirements Checklist: VideoProvider Abstraction

**Branch**: `012-video-providers` | **Date**: 2026-06-16

---

## Functional Requirements

- [ ] **FR-001** — `app/providers/video/base.py` contains `VideoProvider` ABC,
      `VideoRequest`, `VideoResult`, and the four exceptions
      (`VideoProviderError`, `RateLimited`, `Unavailable`, `InvalidOutput`).
      Unit test verifies importability and dataclass immutability (frozen=True).

- [ ] **FR-002** — `HFVideoProvider` calls the documented HF Inference API
      endpoint for `Lightricks/LTX-Video` with bearer auth; `num_frames` is
      derived via `_derive_num_frames(duration_s, fps)` satisfying `n*8+1`;
      `mutagen` parses duration from response bytes; duration < 80% of requested
      raises `InvalidOutput`; 429/503/5xx/timeout correctly map to typed
      exceptions. Mock-based unit test for each error path.

- [ ] **FR-003** — `PollinationsVideoProvider` calls the beta video endpoint;
      no auth; same status translation and duration validation as FR-002;
      `health()` returns `False` (not raises) on any connectivity failure.
      Mock-based unit test for each error path.

- [ ] **FR-004** — `FakeVideoProvider` pops responses in order; supports raising
      injected exceptions (both class and instance); respects `latency_ms`;
      `health_returns` toggle works; list exhaustion raises `VideoProviderUnavailable`.
      `MINIMAL_MP4` constant is parseable by `mutagen.mp4.MP4`. Unit-tested.

- [ ] **FR-005** — `VideoProviderRouter` honors the 9 policy branches:
      (a) success on first provider; (b) `RateLimited` skips immediately;
      (c) `Unavailable` retries with backoff then skips; (d) `InvalidOutput`
      skips no-retry; (e) `health_false` skips; (f) chain exhausted raises
      `VideoProviderUnavailable` chained from last exception; (g) clip within
      80-100% tolerance accepted with warning log; (h) clip below 80% threshold
      raises `InvalidOutput`; (i) `NotImplementedError` propagates immediately
      (misconfigured chain). Nine named unit tests.

- [ ] **FR-006** — All retry/timeout parameters come from `settings.py`
      (`T2V_TIMEOUT_S`, `T2V_MAX_RETRIES`, `T2V_BACKOFF_SECONDS`). Test asserts
      router instantiated with overridden settings uses those values, not defaults.

- [ ] **FR-007** — Seven structured log events emitted (see spec FR-007).
      Captured-log test asserts presence and correct `outcome` value on each
      router policy branch.

- [ ] **FR-008** — `compute_r2_clip_path` is deterministic, content-addressed,
      and conforms to the documented regex
      `^seasons/[a-z0-9-]+/[0-9a-f-]{36}/clips/\d+-[0-9a-f]{8}\.mp4$`.
      Unit test covers idempotency + different bytes → different hash.

- [ ] **FR-009** — `chain_for_env("mvp")` returns `[HFVideoProvider,
      PollinationsVideoProvider]`; `"dev"` returns `[FakeVideoProvider]`;
      `"paid_v1"` raises `NotImplementedError`.

- [ ] **FR-010** — `KlingProvider`, `RunwayProvider`, `LumaProvider` are
      importable, are subclasses of `VideoProvider`, have `capabilities`
      populated, and raise `NotImplementedError` on both `health()` and
      `generate()`. Dedicated unit test for each stub.

- [ ] **FR-011** — Import-graph guard test scans `app/api/`, `app/domain/`,
      `app/scripts/` and passes with no banned URL literals or direct provider
      sub-module imports.

## Non-Functional Requirements

- [ ] **NFR-001** — `health()` of each real provider returns within 2 s.
      (Live test only; not on PR CI.)

- [ ] **NFR-002** — `FakeVideoProvider.generate()` with `latency_ms=0` returns
      in < 5 ms. Measured in unit test.

- [ ] **NFR-003** — Router failover decision overhead (excluding backoff sleep
      and provider latency) < 50 ms. Asserted in router unit test with
      `FakeVideoProvider(latency_ms=0)`.

- [ ] **NFR-004** — Router does NOT cache or retain `bytes_` after returning.
      Confirmed by code review (no instance variable stores `VideoResult`).

## Constitution Gates

- [ ] **Gate 1 — Zero-cost** — HF Inference API free tier; Pollinations unauth;
      `mutagen` MIT, zero runtime cost; paid stubs raise `NotImplementedError`
      (no API calls, no billing).

- [ ] **Gate 2 — Idempotency** — Same `(prompt, seed, duration_s, width, height,
      fps)` → same clip bytes from providers (live test asserts). `compute_r2_clip_path`
      is pure; R2 PUT is idempotent.

- [ ] **Gate 3 — TZ anchoring** — N/A. No timestamps generated in this module.

- [ ] **Gate 4 — Provider abstraction** — **This module IS the abstraction.**
      Import-graph guard test (FR-011) enforces that no consumer imports provider
      internals. Module 008 delta imports only from `app.providers.video`.

- [ ] **Gate 5 — Determinism** — Deterministic seed propagation; `_derive_num_frames`
      is pure; `compute_r2_clip_path` is pure.

- [ ] **Gate 6 — Spanish / English** — All identifiers English; no user-facing
      strings in this module.

- [ ] **Gate 7 — Soft delete** — N/A. No user content managed here.

- [ ] **Gate 8 — Tests from day one** — Unit (T-001 through T-008) + import-graph
      (T-010) ship in the same PR. Live tests (T-009) ship in the same PR gated
      behind `@pytest.mark.live`.

- [ ] **Gate 9 — Trust boundaries** — Provider output validated before
      `VideoResult` is returned: `mime_type == "video/mp4"`, bytes non-empty,
      duration ≥ 80% of requested. Prompts are server-composed; no user input
      reaches provider URLs directly.

- [ ] **Gate 10 — Observability** — Seven structured log events from FR-007.
      Captured-log tests assert each event on its corresponding code path.

## `_derive_num_frames` edge cases

- [ ] `duration_s=5.0, fps=24` → `121` (n=15, standard case).
- [ ] `duration_s=4.0, fps=24` → `97` (n=12).
- [ ] `duration_s=2.0, fps=24` → `49` (n=6).
- [ ] `duration_s=5.1, fps=24` → `121` (rounds down to nearest valid).
- [ ] `duration_s=0.5, fps=24` → `9` (minimum n=1 → 9 frames).
- [ ] `duration_s=5.0, fps=30` → `151` (n=18+1 rounding check).

## Duration validation edge cases

- [ ] `actual=5.0s, requested=5.0s` → accepted (100%).
- [ ] `actual=4.0s, requested=5.0s` → accepted with `video_provider_short_clip`
      warning (80% threshold, boundary).
- [ ] `actual=3.9s, requested=5.0s` → `InvalidOutput` (below 80%).
- [ ] `actual=0s (0-byte response)` → `InvalidOutput`.
- [ ] `actual > requested` → accepted (provider returned more than asked).

## `MINIMAL_MP4` constant

- [ ] `mutagen.mp4.MP4(BytesIO(MINIMAL_MP4))` parses without error.
- [ ] `mutagen.mp4.MP4(BytesIO(MINIMAL_MP4)).info.length > 0`.
- [ ] `len(MINIMAL_MP4) > 0`.

## Documentation

- [ ] Quickstart walked end-to-end (both `mvp` smoke and `dev` fake path).
- [ ] `docs/adr/` placeholder created for future paid-T2V providers
      (`0006-paid-video-providers.md` stub with Ronda 6 #26 reference).
- [ ] `specs/README.md` marks 012 `done`.
- [ ] `SDD.md` Ronda 6 already updated (2026-06-16, done).

## Sign-off

- [ ] Reviewer 1 (engineering) — verify `_derive_num_frames` formula and
      `mutagen` integration.
- [ ] Reviewer 2 (PO) — sanity check on paid stub reservation strategy and
      `manifest_kind` backward-compat rule.
