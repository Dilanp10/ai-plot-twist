# Requirements Checklist: Director's Filter

**Branch**: `006-directors-filter` | **Date**: 2026-06-07

---

## Functional Requirements

- [ ] **FR-001** — `LLMProvider` ABC + typed exceptions exist. Import-graph test
      verifies `app.domain.director_filter` does NOT import any provider SDK
      directly.
- [ ] **FR-002** — `GeminiProvider` calls `gemini-2.0-flash` with
      `response_mime_type='application/json'` + `response_schema`. Mock-based
      unit test asserts SDK call shape.
- [ ] **FR-003** — `GitHubModelsProvider` calls `gpt-4o-mini` via the OpenAI
      SDK pointed at GitHub Models base URL. Unit-tested similarly.
- [ ] **FR-004** — Router fallback policy: retries on `Unavailable`, skips on
      `RateLimited`/`InvalidOutput`. Four named tests cover all branches.
- [ ] **FR-005** — Batching: 50 twists with `DIRECTOR_BATCH_SIZE=25` yields 2 LLM
      calls. Verified in `test_director_filter_e2e.py::test_batching`.
- [ ] **FR-006** — Prompt files versioned + hash audit in
      `test_prompt_hashes_match`. CI fails on prompt edit without constant bump.
- [ ] **FR-007** — `temperature=0.2`, `max_output_tokens=2048` asserted by mock
      assertions on provider calls.
- [ ] **FR-008** — `DirectorBatchResponse` Pydantic model matches
      `contracts/director-response.schema.json` (schema parity test).
- [ ] **FR-009** — Default-deny: omitted twists become `rejected_incoherent` with
      the exact reason string. `test_director_default_deny.py`.
- [ ] **FR-010** — Slur post-filter: `approved` + slur match → `rejected_offensive`.
      `test_slur_list.py` covers 5 entries; `test_director_filter_unit.py` covers
      the override flow.
- [ ] **FR-011** — Persistence: one UPDATE per twist; transaction per batch.
      Integration test asserts rows updated in a single commit.
- [ ] **FR-012** — Cycle transition to `VOTACION` is triggered after all batches.
      Verified by `state_transitions` row appearing.
- [ ] **FR-013** — DI registration verified by startup integration test:
      `side_effects.get("director_filter")` returns the real impl, not the stub.
- [ ] **FR-014** — Admin replay endpoint: re-classifies all chapter twists,
      excludes `deleted_by_user`, returns breakdown.
      `test_director_replay_endpoint.py`.
- [ ] **FR-015** — `pnpm rerun-filter` CLI wraps the endpoint and prints the
      breakdown. Integration test.
- [ ] **FR-016** — All 5 structured log events emitted with documented keys.
      Grep test against captured log output.

## Non-Functional Requirements

- [ ] **NFR-001** — 500 twists processed in ≤ 4 min using `FakeLLMProvider` (with
      simulated latency 1 s/batch).
- [ ] **NFR-002** — Batch p95 < 15 s with `FakeLLMProvider`-injected latency.
- [ ] **NFR-003** — Fallover decision ≤ 200 ms.
- [ ] **NFR-004** — `llm_budget_warn` triggers at 70 % of `LLM_BUDGET_PER_DAY` env
      var.

## Constitution Gates

- [ ] **Gate 1 — Zero-cost** — Gemini + GitHub Models free tiers. Budget warning
      live.
- [ ] **Gate 2 — Idempotency** — Re-run filter on same chapter produces consistent
      verdicts (overwrites with same model output assuming deterministic temp 0.2).
- [ ] **Gate 3 — TZ anchoring** — N/A.
- [ ] **Gate 4 — Provider abstraction** — Central to this module. Verified by
      import-graph test.
- [ ] **Gate 5 — Determinism** — `temperature=0.2` + pinned model version +
      `response_schema`. Reproducibility test with FakeLLM.
- [ ] **Gate 6 — Spanish / English** — Code English; prompts Spanish.
- [ ] **Gate 7 — Soft delete** — Filter never touches `deleted_by_user` twists.
- [ ] **Gate 8 — Tests from day one** — Unit + integration + live (manual)
      tests all ship.
- [ ] **Gate 9 — Trust boundaries** — LLM output validated against Pydantic;
      slur post-filter is the second defense layer; admin endpoint requires
      `ADMIN_TOKEN`.
- [ ] **Gate 10 — Observability** — All 5 events live.

## Documentation

- [ ] Quickstart walked end-to-end with both Gemini and GitHub Models reachable.
- [ ] `specs/README.md` marks module `done`; marks 007 `in-progress`.
- [ ] Onboarding screen note about IA moderation added to PWA (out of this module's
      diff scope — file an issue if not already done).

## Sign-off

- [ ] Reviewer 1 (engineering)
- [ ] Reviewer 2 (PO)
