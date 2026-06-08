# Implementation Plan: Director's Filter

**Branch**: `006-directors-filter` | **Date**: 2026-06-07 | **Spec**: [spec.md](./spec.md)
**Depends on**: `003-cycle-fsm`, `005-twists-submission`

## Summary

Three components ship in this module:

1. **`LLMProvider` abstraction** — interface + Gemini impl + GitHub Models impl +
   router + Fake (for tests). Reused by module 008.
2. **Director filter** — orchestrator that batches `pending_review` twists,
   calls the router, applies default-deny + slur post-filter, persists verdicts,
   transitions cycle to `VOTACION`. Replaces the stub from module 003 via DI.
3. **Admin replay endpoint + CLI** — `POST /internal/director/replay` and
   `pnpm rerun-filter`.

No new tables. Updates `twists` rows. Adds two prompt files.

## Technical Context

**Languages/Versions**: Python 3.11.
**New API dependencies**:
- `google-genai ~=0.7` (Gemini official SDK).
- `openai ~=1.40` (for GitHub Models endpoint compatibility — Anthropic-free).
- `jinja2 ~=3.1` (already transitive via FastAPI, declared explicitly here).
**Storage**: read+update on `twists`; read on `seasons`, `chapters`.
**Testing**: `FakeLLMProvider` in unit tests; real Gemini calls behind `@live_llm`
mark (manual / nightly only).
**Project type**: same.
**Performance Goals**: see NFR-001..NFR-004.
**Constraints**: free-tier Gemini (15 RPM, 1500 RPD); GitHub Models personal-use only.
**Scale/Scope**: ≤ 1 filter run/day in MVP, ≤ 300 twists/run.

## Constitution Check

### Gate 1 — Zero-cost
- [x] Gemini free tier; GitHub Models free for personal use.
      `llm_budget_warn` log when ≥ 70 % of daily quota consumed.

### Gate 2 — Idempotency
- [x] Filter is naturally idempotent on `(chapter_id, twist_id)`: re-running
      overwrites verdicts but doesn't duplicate side effects (no twist creation,
      no state-machine double-advance — the FSM transition to `VOTACION` is
      idempotent at the executor level).
- [x] Admin replay endpoint can be called repeatedly safely.

### Gate 3 — TZ anchoring
- [x] N/A — no time-of-day logic. Module 003 owns the schedule.

### Gate 4 — Provider abstraction
- [x] **Central to this module.** `LLMProvider` interface; no
      `google.generativeai.GenerativeModel(...)` or `OpenAI(...)` calls in the
      filter body. Tests assert the import graph: `app.domain.director_filter`
      does NOT import `google_genai` or `openai` directly.

### Gate 5 — Determinism
- [x] `temperature=0.2`, model versions pinned (`gemini-2.0-flash`,
      `gpt-4o-mini`), `response_schema` enforced.
- [x] Same input batch → same verdicts with high probability. CI runs a
      reproducibility test against the FakeLLM (deterministic by construction).

### Gate 6 — Spanish / English
- [x] Identifiers English. Prompts in Spanish (per SDD §4.2.2).
- [x] `director_reason` is Spanish, ≤ 80 chars.

### Gate 7 — Soft delete
- [x] Filter does NOT touch `deleted_by_user` twists (they're excluded by the
      `WHERE status='pending_review'` selector).

### Gate 8 — Tests from day one
- [x] Unit: prompt rendering, default-deny, slur override, batch chunking,
      reason truncation.
- [x] Integration with FakeLLM: full filter cycle, partial response,
      LLM-omit-twist, all-providers-down.
- [x] Live test (manual / nightly CI) hits Gemini for one batch of 3 synthetic
      twists; asserts non-empty response.

### Gate 9 — Trust boundaries
- [x] LLM output validated against Pydantic schema before any DB write.
- [x] Slur post-filter is the second defense layer (Gate 9: "LLM outputs treated
      as untrusted input").
- [x] Admin replay endpoint requires `ADMIN_TOKEN`.

### Gate 10 — Observability
- [x] `filter_started`, `llm_batch`, `llm_provider_failover`,
      `slur_override_applied`, `filter_completed` events all documented in FR-016.

## Project Structure

```text
specs/006-directors-filter/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── contracts/
│   └── director-response.schema.json   ← JSON Schema for LLM response
├── quickstart.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

```text
apps/api/
├── app/
│   ├── providers/                          ← NEW namespace
│   │   ├── __init__.py
│   │   └── llm/
│   │       ├── __init__.py
│   │       ├── base.py                     ← ABC, dataclasses, exceptions
│   │       ├── gemini.py                   ← GeminiProvider
│   │       ├── github_models.py            ← GitHubModelsProvider
│   │       ├── router.py                   ← LLMProviderRouter
│   │       └── fake.py                     ← FakeLLMProvider for tests
│   ├── domain/
│   │   ├── director_filter.py              ← orchestrator (replaces stub)
│   │   ├── director_prompts.py             ← load + render templates
│   │   ├── director_verdicts.py            ← Pydantic models for response
│   │   └── slur_list.py                    ← curated Spanish slur regex
│   ├── api/
│   │   └── internal_director_replay.py     ← admin POST endpoint
│   ├── scripts/
│   │   └── rerun_filter.py                 ← CLI
│   ├── prompts/
│   │   ├── director_v1.system.txt
│   │   └── director_v1.user.j2
│   ├── settings.py                         ← MODIFIED (add GEMINI_API_KEY etc.)
│   └── main.py                             ← MODIFIED (DI registration)
└── tests/
    ├── unit/
    │   ├── test_llm_provider_router.py
    │   ├── test_director_prompts.py
    │   ├── test_director_verdicts.py
    │   ├── test_slur_list.py
    │   └── test_director_filter_unit.py    ← uses FakeLLM
    ├── integration/
    │   ├── test_director_filter_e2e.py
    │   ├── test_director_default_deny.py
    │   ├── test_director_fallback.py
    │   ├── test_director_replay_endpoint.py
    │   └── test_director_all_providers_down.py
    └── live/
        └── test_gemini_smoke.py            ← @pytest.mark.live
```

## Phase 0 — Research

See [research.md](./research.md). Key decisions:

- LLM abstraction analogous to ImageProvider; pinned model versions.
- Prompt versioning by file (not DB) with semver in file names.
- Default-deny semantics: fail-closed.
- Slur post-filter as defense in depth (Gate 9).
- Free-tier budget tracking via structured logs (no DB).
- `FakeLLMProvider` is the test default; real providers gated behind env.

## Phase 1 — Design Artefacts

- [contracts/director-response.schema.json](./contracts/director-response.schema.json) — the JSON Schema enforced by `response_schema` on Gemini.
- [data-model.md](./data-model.md) — no schema changes; documents the UPDATE pattern and the prompts directory contract.
- [quickstart.md](./quickstart.md).
- [checklists/requirements.md](./checklists/requirements.md).
- [tasks.md](./tasks.md).

## Phase 2 — Implementation Sequence

1. **T-001..T-003** — `LLMProvider` ABC + Fake + router (unit-tested standalone).
2. **T-004..T-005** — GeminiProvider + GitHubModelsProvider (each tested with mocks).
3. **T-006..T-007** — Prompt loader + verdicts Pydantic models.
4. **T-008** — Slur list module.
5. **T-009** — Director filter orchestrator.
6. **T-010** — DI registration in `main.py`.
7. **T-011** — Admin replay endpoint.
8. **T-012** — `pnpm rerun-filter` CLI.
9. **T-013..T-015** — Integration tests (e2e, default-deny, fallback, all-down).
10. **T-016** — Live smoke against real Gemini (one batch, 3 twists).
11. **T-017** — Deploy + observe.

## Risks & Mitigations (feature-local)

| ID | Risk | Mitigation |
|---|---|---|
| **R-D1** | Gemini changes API or deprecates the model | Pin SDK version; monitor `llm_provider_failover` logs; fallback already in place |
| **R-D2** | Prompt drift between code and prompts/*.txt files | Files versioned in git; CI test computes sha256 of each prompt file and compares to expected (catches accidental edits) |
| **R-D3** | LLM cost surprise (free tier removed) | Daily budget log warns at 70 %; PO can disable filter via kill-switch; fallback to "approve all" stub via env var |
| **R-D4** | Slur list false-positives | Conservative list; PO can hot-patch via redeploy; misclassified twists can be manually fixed in DB |
| **R-D5** | Prompt injection succeeds despite slur filter | Out of scope to fully solve; document. PO bans the user; rerun filter |
| **R-D6** | LLM call slow (15 s/batch) blocks the FSM | Filter task is BackgroundTask; FSM is in FILTERING during the wait; watchdog at 23:55 catches truly stuck runs |

## Post-Conditions

After merge:
- The 18:00 cron tick triggers real LLM moderation.
- Module 007 (voting) finds approved twists ready in the DB.
- Module 008 (generation) can import `LLMProvider` and reuse the same Gemini setup
  for the scriptwriter step.
