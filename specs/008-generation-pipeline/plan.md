# Implementation Plan: Nightly Generation Pipeline

**Branch**: `008-generation-pipeline` | **Date**: 2026-06-07 | **Spec**: [spec.md](./spec.md)
**Depends on**: `003-cycle-fsm`, `006-directors-filter`, `007-voting`,
                `009-image-providers`

## Summary

The largest orchestration module of the project. Replaces the
`generation_pipeline_stub` from module 003 with the real flow: deterministic
winner selection → scriptwriter LLM → parallel panel rendering with TTS → R2
upload → atomic persistence → cycle transition. Honors a hard deadline of 55 min
and degrades gracefully on partial failures.

Architecture in five layers:

1. **Winner selection** (`winner_selector.py`) — pure SQL with tiebreak.
2. **Scriptwriter** (`scriptwriter.py`) — `LLMProvider` consumer; produces a
   `ScriptwriterResponse` Pydantic model.
3. **Panel pipeline** (`panel_pipeline.py`) — per-panel orchestration: T2I via
   `ImageProviderRouter`, optional TTS via Edge-TTS, R2 upload.
4. **Pipeline coordinator** (`generation_pipeline.py`) — top-level orchestrator
   with `asyncio.gather`, deadline watch, exception aggregation.
5. **Persistence + transition** — single transactional `INSERT chapters` +
   `UPDATE cycles` + cycle FSM transition.

No new tables. Two new prompt files. One new infra (R2 uploader).

## Technical Context

**Languages/Versions**: Python 3.11.
**New deps**:
- `boto3 ~=1.34` (S3-compatible client for R2).
- `edge-tts ~=6.1` (Microsoft Edge TTS endpoint wrapper).
**Storage**: read on `twists`, `votes`, `seasons`, `chapters`; insert on
`chapters`; update on `cycles`. Writes to R2 (assets bucket).
**Testing**: `FakeLLMProvider` + `FakeImageProvider` cover full pipeline in CI;
real provider tests behind `@pytest.mark.live`.
**Project type**: same.
**Performance Goals**: see NFR-001..NFR-004.
**Constraints**: 256 MB RAM, 55 min hard deadline, free-tier R2 (10 GB lifetime).
**Scale/Scope**: 1 generation/day × 3-4 panels = ≤ ~150 generations + ~600
panels in a 5-month closed beta.

## Constitution Check

### Gate 1 — Zero-cost
- [x] No paid services. Edge-TTS uses Microsoft's public endpoint (no auth).
      R2 free tier.

### Gate 2 — Idempotency
- [x] FSM advisory lock + UNIQUE `(cycle, to_state, trigger_id)` from module
      003 prevent double-fire of the pipeline.
- [x] R2 upload key is content-addressed (module 009 path scheme) → re-upload
      is idempotent at the storage level.
- [x] Persistence is a single transaction; partial failures roll back.

### Gate 3 — TZ anchoring
- [x] N/A.

### Gate 4 — Provider abstraction
- [x] Pipeline imports `LLMProvider` from 006 and `ImageProviderRouter` from
      009. NO direct calls to Gemini/Pollinations/HF. Import-graph guards in
      006 and 009 enforce.

### Gate 5 — Determinism
- [x] **Critical for this module.** Winner selection is fully deterministic;
      reproducibility test runs the SQL 10× with same data → same winner.
- [x] T2I seeds derived from `stable_hash(chapter_id, panel_idx)` per FR-004.
- [x] Scriptwriter at `temperature=0.6` — NOT deterministic by design (creativity
      desired). This is the only non-deterministic step; documented in
      research R-005.

### Gate 6 — Spanish / English
- [x] Identifiers English. Prompts Spanish (per SDD §4.2.2). Default-deny reasons
      Spanish.

### Gate 7 — Soft delete
- [x] Winner selection excludes `deleted_by_user` twists via the
      `status='approved'` filter.

### Gate 8 — Tests from day one
- [x] Unit: winner selector (with synthetic data), scriptwriter prompt
      rendering, manifest builder, R2 path computation (delegated to 009).
- [x] Integration: full pipeline with Fake providers (8 scenarios: happy,
      tie, no-winner, panel-failure, scriptwriter-failure, deadline,
      r2-failure, rerun).
- [x] Live (nightly): one full pipeline run against real providers and a test
      R2 bucket.

### Gate 9 — Trust boundaries
- [x] LLM outputs validated against Pydantic `ScriptwriterResponse` before any
      panel work.
- [x] Image URLs from R2 are not user-trusted; we only return URLs that we
      uploaded (no user-supplied URLs anywhere).
- [x] Admin replay endpoint requires `ADMIN_TOKEN`.

### Gate 10 — Observability
- [x] Nine structured events documented in FR-015.
- [x] Total pipeline duration recorded; submitted to Discord on
      `ready_degraded` or FAILED.

## Project Structure

```text
specs/008-generation-pipeline/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── contracts/
│   ├── scriptwriter-response.schema.json
│   └── manifest-shape.md                ← documents manifest_json contract
├── quickstart.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

```text
apps/api/
├── app/
│   ├── prompts/
│   │   ├── scriptwriter_v1.system.txt
│   │   ├── scriptwriter_v1_auto.system.txt
│   │   └── scriptwriter_v1.user.j2
│   ├── domain/
│   │   ├── winner_selector.py          ← pure SQL + tiebreak
│   │   ├── scriptwriter.py             ← LLMProvider consumer
│   │   ├── scriptwriter_response.py    ← Pydantic models
│   │   ├── manifest_builder.py         ← assembles chapter manifest_json
│   │   ├── panel_pipeline.py           ← per-panel orchestrator
│   │   ├── generation_pipeline.py      ← top-level coordinator (replaces stub)
│   │   ├── tts_synthesizer.py          ← edge-tts wrapper
│   │   └── seed_derivation.py          ← stable_hash(chapter_id, panel_idx)
│   ├── infra/
│   │   └── r2_uploader.py              ← boto3 S3-compatible
│   ├── api/
│   │   └── internal_generation_rerun.py
│   ├── scripts/
│   │   └── rerun_generation.py         ← CLI
│   ├── settings.py                     ← MODIFIED (R2_*, SCRIPTWRITER_*, TTS_*)
│   └── main.py                         ← MODIFIED (DI registration)
└── tests/
    ├── unit/
    │   ├── test_winner_selector.py
    │   ├── test_scriptwriter_prompts.py
    │   ├── test_scriptwriter_response.py
    │   ├── test_manifest_builder.py
    │   ├── test_seed_derivation.py
    │   └── test_tts_synthesizer.py
    ├── integration/
    │   ├── test_generation_happy.py
    │   ├── test_generation_tie.py
    │   ├── test_generation_no_winner.py
    │   ├── test_generation_panel_failure.py
    │   ├── test_generation_scriptwriter_failure.py
    │   ├── test_generation_deadline.py
    │   ├── test_generation_r2_failure.py
    │   ├── test_generation_rerun.py
    │   └── test_generation_rerun_endpoint.py
    └── live/
        └── test_full_pipeline_smoke.py
```

## Phase 0 — Research

See [research.md](./research.md). Key decisions:

- Winner SQL fully reuses SDD §4.3.
- Auto-continue handled by a separate system prompt variant (not a parameter
  switch).
- Per-panel concurrency bounded by `asyncio.Semaphore(PANEL_CONCURRENCY)`.
- Deadline watcher as a separate `asyncio.Task` racing `pipeline_task`.
- `boto3` over `aioboto3` (justification in R-007).
- TTS as fire-and-forget per panel: failure logged, doesn't block.
- Atomic persistence at the end (no incremental writes).
- Manifest schema versioned via `manifest_json.schema_version`.

## Phase 1 — Design Artefacts

- [contracts/scriptwriter-response.schema.json](./contracts/scriptwriter-response.schema.json).
- [contracts/manifest-shape.md](./contracts/manifest-shape.md).
- [data-model.md](./data-model.md).
- [quickstart.md](./quickstart.md).
- [checklists/requirements.md](./checklists/requirements.md).
- [tasks.md](./tasks.md).

## Phase 2 — Implementation Sequence

1. **T-001..T-003** — Pure pieces: winner selector, seed derivation, scriptwriter
   response models.
2. **T-004..T-005** — Prompt files + scriptwriter consumer.
3. **T-006** — Manifest builder.
4. **T-007** — R2 uploader (infra).
5. **T-008** — TTS synthesizer.
6. **T-009** — Panel pipeline.
7. **T-010** — Generation pipeline coordinator + deadline watcher.
8. **T-011** — DI registration in `main.py`.
9. **T-012** — Admin rerun endpoint.
10. **T-013** — CLI.
11. **T-014..T-018** — Integration tests (8 scenarios).
12. **T-019** — Live smoke.
13. **T-020** — Deploy + observe one full cycle.

## Risks & Mitigations (feature-local)

| ID | Risk | Mitigation |
|---|---|---|
| **R-G1** | Pipeline crashes leaving partial R2 uploads + no DB row | Storage is content-addressed and free; orphans are harmless. Cycle transitions to FAILED via `safe_side_effect`; rerun overwrites. |
| **R-G2** | `boto3` blocks the event loop | Run uploads in a `ThreadPoolExecutor` (research R-007). |
| **R-G3** | Edge-TTS endpoint changes / rate-limits | Library wraps the public endpoint; if it breaks, disable TTS via `TTS_ENABLED=false`. Acceptable degradation (audio is optional UX). |
| **R-G4** | Scriptwriter produces visual_prompts in Spanish (poor T2I quality) | Prompt explicitly demands `visual_prompt` in English; Pydantic validator can enforce a simple "ASCII heuristics" check. Documented in research R-002. |
| **R-G5** | Character visual consistency between chapters | SDD §8 R-3 acknowledges this. Out of scope; document. |
| **R-G6** | Deadline watcher races persist + transition | Watcher cancels panel tasks via `task.cancel()` and waits for the pipeline coordinator to finalize; only the coordinator writes to DB. Single writer. |
| **R-G7** | R2 free-tier 10 GB exhausted | At ~3 MB/panel × 4 panels × 365 days = ~4 GB/year. Comfortably under. Discord alert at 80 % usage (future module). |

## Post-Conditions

After merge:
- 23:00 ART cron triggers real generation; new chapter is `ready` by ~23:50.
- The loop is closed: filter → vote → generate → release → filter → vote → …
- Module 010 (PWA polish) and 011 (push) can rely on a steady stream of real
  chapters in `PENDING_RELEASE`.
