# Requirements Checklist: ImageProvider Abstraction

**Branch**: `009-image-providers` | **Date**: 2026-06-07

---

## Functional Requirements

- [ ] **FR-001** — `app/providers/image/base.py` contains the ABC, the two
      dataclasses, and the four exceptions. Unit test verifies importability.
- [ ] **FR-002** — `PollinationsProvider` calls `image.pollinations.ai` with the
      documented URL pattern; respects `T2I_TIMEOUT_S`; correctly maps
      429/5xx/timeout to typed exceptions. Mock-based unit test for each path.
- [ ] **FR-003** — `HuggingFaceProvider` calls the documented HF Inference
      endpoint with bearer auth. JSON body includes seed/width/height. Maps
      429/503/timeout to typed exceptions.
- [ ] **FR-004** — `FakeImageProvider` pops responses in order, supports raising
      injected exceptions, respects `latency_ms`, and `health_returns` toggle.
      Unit-tested.
- [ ] **FR-005** — `ImageProviderRouter` honors the 6 policy branches:
      (a) success on first; (b) RateLimited skips; (c) Unavailable retries with
      backoff; (d) InvalidOutput skips no-retry; (e) health_false skips; (f)
      chain exhausted raises chained exception. Six named unit tests.
- [ ] **FR-006** — Backoff parameters come from `settings.py`. Test asserts
      router instantiated with overridden settings uses those values.
- [ ] **FR-007** — Five structured log events emitted (see spec).
      Captured-log test asserts presence on each branch.
- [ ] **FR-008** — `compute_r2_path` is deterministic and conforms to the
      documented regex.
- [ ] **FR-009** — `chain_for_env("mvp"|"dev")` returns concrete chains;
      `"v02"` raises `NotImplementedError` with the docs link.
- [ ] **FR-010** — Import-graph guard test passes for all current consumers
      (none yet outside `app.providers.image`); will be exercised by module 008.

## Non-Functional Requirements

- [ ] **NFR-001** — `health()` of each real provider returns within 2 s.
      (Live test, not on PR CI.)
- [ ] **NFR-002** — `FakeImageProvider.generate(0-latency)` returns in < 5 ms.
- [ ] **NFR-003** — Router failover decision (no backoff sleep counted) < 50 ms.
- [ ] **NFR-004** — Router does NOT cache or retain `bytes_` after returning.
      Inspected via heap profiling in a long-running test.

## Constitution Gates

- [ ] **Gate 1 — Zero-cost** — Pollinations unauth; HF free tier.
- [ ] **Gate 2 — Idempotency** — Same `(prompt, seed, w, h)` → same image bytes
      from real providers (live test asserts).
- [ ] **Gate 3 — TZ anchoring** — N/A.
- [ ] **Gate 4 — Provider abstraction** — **This module IS the abstraction.**
      Import-graph guard test enforces.
- [ ] **Gate 5 — Determinism** — Deterministic seed propagation + deterministic
      `compute_r2_path`.
- [ ] **Gate 6 — Spanish / English** — No user-facing strings.
- [ ] **Gate 7 — Soft delete** — N/A.
- [ ] **Gate 8 — Tests from day one** — Unit + live + import-graph all ship.
- [ ] **Gate 9 — Trust boundaries** — Provider outputs validated (mime in
      allowlist; non-empty bytes).
- [ ] **Gate 10 — Observability** — Five events documented in FR-007.

## Documentation

- [ ] Quickstart walked end-to-end.
- [ ] `docs/adr/0003-image-provider-v02.md` placeholder created with the
      LocalComfy integration plan (stub OK).
- [ ] `specs/README.md` marks 009 `done`.

## Sign-off

- [ ] Reviewer 1 (engineering)
- [ ] Reviewer 2 (PO) — sanity check on the LocalComfy reservation.
