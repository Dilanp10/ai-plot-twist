# Task Breakdown: ImageProvider Abstraction

**Branch**: `009-image-providers` | **Date**: 2026-06-07

---

## Phase 0 — Base types (1 PR)

### T-001 — Base ABC + dataclasses + exceptions → 001-merged
**Files**:
- `apps/api/app/providers/__init__.py` (extend; may already exist from 006)
- `apps/api/app/providers/image/__init__.py`
- `apps/api/app/providers/image/base.py`
- `apps/api/tests/unit/test_image_request.py`

**Body**: as in [contracts/image-providers.md](./contracts/image-providers.md).

---

## Phase 1 — Implementations (3 PRs)

### T-002 — `FakeImageProvider` → T-001
**Files**:
- `apps/api/app/providers/image/fake.py`
- `apps/api/tests/unit/test_fake_provider.py`

**Constants**: `PNG_1x1` (the 95-byte 1×1 transparent PNG).

### T-003 — `PollinationsProvider` → T-001 [P]
**Files**:
- `apps/api/app/providers/image/pollinations.py`
- `apps/api/tests/unit/test_pollinations_provider.py`

**Behavior**:
- `health()`: HTTP GET `/` with 2 s timeout; True if status < 500.
- `generate(req)`: URL pattern from SDD §4.4; `stream`-style GET; verify
  content-type matches `image/*` and bytes > 0; raise typed exceptions on
  failure.

### T-004 — `HuggingFaceProvider` → T-001 [P]
**Files**:
- `apps/api/app/providers/image/huggingface.py`
- `apps/api/tests/unit/test_huggingface_provider.py`

**Behavior**:
- POST `https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-schnell`.
- Bearer `HUGGINGFACE_TOKEN`.
- Body: `{"inputs": prompt, "parameters": {"seed", "width", "height"}}`.
- 503 with body containing `"estimated_time"` → `Unavailable` (cold start).

---

## Phase 2 — Router (1 PR)

### T-005 — `ImageProviderRouter` → T-002
**Files**:
- `apps/api/app/providers/image/router.py`
- `apps/api/tests/unit/test_image_router.py`

**Tests** (all six branches from FR-005):

- `test_success_on_first_provider`
- `test_rate_limited_skips_to_next`
- `test_unavailable_retries_then_succeeds` (with sleep mocked via
  `monkeypatch.setattr("asyncio.sleep", AsyncMock())`)
- `test_unavailable_all_providers_exhausted`
- `test_invalid_output_skips_no_retry`
- `test_health_false_skips_no_attempt`

---

## Phase 3 — Helpers (2 PRs, parallel)

### T-006 — `compute_r2_path` → T-001 [P]
**Files**:
- `apps/api/app/providers/image/paths.py`
- `apps/api/tests/unit/test_image_paths.py`

### T-007 — `chain_for_env` factory + LocalComfy stub → T-002..T-004 [P]
**Files**:
- `apps/api/app/providers/image/__init__.py` (extend)
- `apps/api/app/providers/image/local_comfy.py` (stub raising NotImplementedError)
- `apps/api/tests/unit/test_chain_for_env.py`
- `docs/adr/0003-image-provider-v02.md` (skeleton)

---

## Phase 4 — Guard + live (2 PRs)

### T-008 — Import-graph guard test → T-001..T-007
**Files**:
- `apps/api/tests/unit/test_image_import_graph.py`

**Implementation**: walks `app/api`, `app/domain`, `app/scripts`; for each
file, asserts none of the banned literals appear and no module-level import
references `app.providers.image.pollinations` / `.huggingface` directly.

### T-009 — Live smoke tests → T-003, T-004
**Files**:
- `apps/api/tests/live/test_pollinations_smoke.py`
- `apps/api/tests/live/test_huggingface_smoke.py`
- `.github/workflows/live-llm-smoke.yml` (extend from module 006)

**Behavior**: tagged `@pytest.mark.live`; one render each; assert non-empty
bytes + correct mime_type; skipped on PR CI.

---

## Phase 5 — Settings + docs (1 PR)

### T-010 — Settings knobs + module integration
**Files**:
- `apps/api/app/settings.py` (add `T2I_TIMEOUT_S`, `T2I_MAX_RETRIES`,
  `T2I_BACKOFF_SECONDS_CSV`, `HUGGINGFACE_TOKEN`)
- `.env.example` (extend)
- `specs/README.md` (mark 009 done; 008 in-progress)

---

## Done-when (module-level acceptance)

1. All 10 tasks merged.
2. Every box in [checklists/requirements.md](./checklists/requirements.md) ticked.
3. Live tests pass against both providers (manual or nightly).
4. Import-graph guard test green; module 008 will respect it.

---

## Estimates (solo dev, calendar days)

| Phase | Tasks | Est. days |
|---|---|---|
| 0 — Base | T-001 | 0.5 |
| 1 — Impls | T-002..T-004 | 2 |
| 2 — Router | T-005 | 1.5 |
| 3 — Helpers | T-006..T-007 | 1 |
| 4 — Guard + live | T-008..T-009 | 1 |
| 5 — Settings | T-010 | 0.5 |
| **Total** | 10 tasks | **≈ 6.5 days** |

Buffer +20% → **plan for 8 working days**.
