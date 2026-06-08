# Implementation Plan: ImageProvider Abstraction

**Branch**: `009-image-providers` | **Date**: 2026-06-07 | **Spec**: [spec.md](./spec.md)
**Depends on**: `001-project-bootstrap`

## Summary

Pure infrastructure module. Defines `ImageProvider` ABC + 2 production
implementations (Pollinations, HuggingFace) + Fake for tests + Router with
typed-exception-driven fallback policy. No HTTP, no DB, no FSM. Consumed by
module 008.

## Technical Context

**Languages/Versions**: Python 3.11.
**New deps**:
- `httpx ~=0.27` (already in deps for module 001).
- No HuggingFace SDK; raw HTTP via `httpx` to keep the dependency surface small
  and the implementation transparent.
**Storage**: none.
**Testing**: `FakeImageProvider` covers all tests in CI; `@pytest.mark.live`
guards real-API tests.
**Project type**: same.
**Performance Goals**: see NFR-001..NFR-004.
**Constraints**: zero cost (Pollinations is unauth; HF free tier needs token).
**Scale/Scope**: ~3–4 render calls per nightly generation, < 1 hr total.

## Constitution Check

### Gate 1 — Zero-cost
- [x] Pollinations free; HF Inference API free tier.

### Gate 2 — Idempotency
- [x] `render(req)` with the same seed is naturally idempotent at the
      Pollinations URL level (seed → same image). HF respects seed too.
- [x] No mutation; not state-bearing.

### Gate 3 — TZ anchoring
- [x] N/A.

### Gate 4 — Provider abstraction
- [x] **This module IS the abstraction.** Tests assert (a) the consumer
      surface is the ABC; (b) no business module imports
      `httpx.AsyncClient(base_url='https://image.pollinations.ai/...')`
      directly.

### Gate 5 — Determinism
- [x] Same `(prompt, seed, w, h)` → same image bytes from Pollinations.
- [x] `compute_r2_path` is pure.

### Gate 6 — Spanish / English
- [x] Identifiers English.
- [x] No user-facing strings (no UI in this module).

### Gate 7 — Soft delete
- [x] N/A.

### Gate 8 — Tests from day one
- [x] Unit: each provider mock-based; router fallback table coverage; Fake
      determinism; `compute_r2_path`.
- [x] Live (manual / nightly): one Pollinations request + one HF request.

### Gate 9 — Trust boundaries
- [x] Provider output validated before returning (`ImageResult.bytes_` non-
      empty; `mime_type` in allowlist). Untrusted by default.
- [x] No URL fragments from user input flow into the abstraction. Module 008
      composes prompts that the user sees, but the **provider** receives only
      a server-built request.

### Gate 10 — Observability
- [x] Five structured events documented in spec FR-007.

## Project Structure

```text
specs/009-image-providers/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── contracts/
│   └── image-providers.md       ← interface documentation (no OpenAPI; this is an internal module)
├── quickstart.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

```text
apps/api/
├── app/
│   ├── providers/
│   │   ├── image/
│   │   │   ├── __init__.py            ← chain_for_env factory
│   │   │   ├── base.py                ← ABC + dataclasses + exceptions
│   │   │   ├── pollinations.py
│   │   │   ├── huggingface.py
│   │   │   ├── fake.py
│   │   │   ├── router.py
│   │   │   └── paths.py               ← compute_r2_path
│   │   └── (llm/ from module 006 sits alongside)
│   └── settings.py                    ← MODIFIED (T2I_* knobs)
└── tests/
    ├── unit/
    │   ├── test_image_request.py
    │   ├── test_image_router.py
    │   ├── test_image_paths.py
    │   ├── test_pollinations_provider.py
    │   ├── test_huggingface_provider.py
    │   └── test_fake_provider.py
    └── live/
        ├── test_pollinations_smoke.py
        └── test_huggingface_smoke.py
```

## Phase 0 — Research

See [research.md](./research.md). Key decisions:

- Raw `httpx` over SDK (avoid dependency creep).
- `httpx.AsyncClient.stream` for byte streaming (memory-bounded).
- Exception taxonomy aligned with SDD §4.5.1 (3 named subclasses).
- Backoff parameters in settings, not hardcoded in router.
- Determinism: explicit seed propagation to every provider.
- `compute_r2_path` lives here (next to providers), not in module 008.

## Phase 1 — Design Artefacts

- [contracts/image-providers.md](./contracts/image-providers.md) — interface contract.
- [data-model.md](./data-model.md) — no schema, but documents the path-derivation contract for R2 keys.
- [quickstart.md](./quickstart.md).
- [checklists/requirements.md](./checklists/requirements.md).
- [tasks.md](./tasks.md).

## Phase 2 — Implementation Sequence

1. **T-001** — Base ABC + dataclasses + exceptions.
2. **T-002** — `FakeImageProvider`.
3. **T-003** — `PollinationsProvider`.
4. **T-004** — `HuggingFaceProvider`.
5. **T-005** — `ImageProviderRouter`.
6. **T-006** — `compute_r2_path` helper.
7. **T-007** — `chain_for_env` factory.
8. **T-008** — Live tests behind `@pytest.mark.live`.
9. **T-009** — Import-graph guard test.

## Risks & Mitigations

| ID | Risk | Mitigation |
|---|---|---|
| **R-IP1** | Pollinations.ai changes URL scheme or auth model | URL pattern centralized in `pollinations.py`; one-line change. Documented in research R-001. |
| **R-IP2** | HF Inference API serves the model in "cold start" mode (503) for the first call | Treated as `Unavailable` → backoff. The router's first attempt may be slow; subsequent calls are warm. |
| **R-IP3** | Module 008 sneaks a direct `httpx` call to a provider | Import-graph test (FR-010) prevents this at CI level. |
| **R-IP4** | `LocalComfyProvider` requirements drift before v0.2 | The factory raises `NotImplementedError` with the doc URL. Spec is forward-looking only. |

## Post-Conditions

After merge:
- Module 008 imports `from app.providers.image import ImageProviderRouter,
  ImageRequest, compute_r2_path, chain_for_env`.
- 008 calls `router = chain_for_env("mvp")` at startup; renders each panel via
  `router.render(...)`.
- Tests in 008 use `FakeImageProvider` to inject deterministic bytes.
- v0.2 ships `LocalComfyProvider` and updates `chain_for_env("v02")` — zero
  changes required in 008.
