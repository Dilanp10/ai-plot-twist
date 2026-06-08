# Task Breakdown: Director's Filter

**Branch**: `006-directors-filter` | **Date**: 2026-06-07

---

## Phase 0 — LLM Provider abstraction (3 PRs)

### T-001 — `LLMProvider` base + Fake [P]
**Files**:
- `apps/api/app/providers/__init__.py`
- `apps/api/app/providers/llm/__init__.py`
- `apps/api/app/providers/llm/base.py`
- `apps/api/app/providers/llm/fake.py`
- `apps/api/tests/unit/test_llm_provider_base.py`

**API**: as documented in [research.md R-001](./research.md#r-001--llm-provider-abstraction-shape).

**FakeLLMProvider** API:
```python
class FakeLLMProvider(LLMProvider):
    name = "fake"
    def __init__(self, responses: list[BaseModel | Exception], latency_ms: int = 0): ...
```
Pops responses in order; supports raising injected exceptions.

### T-002 — `GeminiProvider` → T-001 [P]
**Files**:
- `apps/api/app/providers/llm/gemini.py`
- `apps/api/tests/unit/test_gemini_provider.py`

**Behavior**: wraps `google.genai.Client`; passes `response_schema` for JSON-mode.
Translates Google exceptions to typed ones.

### T-003 — `GitHubModelsProvider` → T-001 [P]
**Files**:
- `apps/api/app/providers/llm/github_models.py`
- `apps/api/tests/unit/test_github_models_provider.py`

**Behavior**: uses `openai` SDK with `base_url='https://models.inference.ai.azure.com'`
and `api_key=GITHUB_MODELS_TOKEN`. JSON mode via `response_format={"type":
"json_object"}` + post-validation against the Pydantic schema (GitHub Models doesn't
support `response_schema` natively as of 2026-06).

### T-004 — `LLMProviderRouter` → T-001
**Files**:
- `apps/api/app/providers/llm/router.py`
- `apps/api/tests/unit/test_llm_provider_router.py`

**Behavior**: fallback policy per FR-004. Tests cover all 4 exception branches +
healthy-provider-skip path.

---

## Phase 1 — Domain pieces (5 PRs, parallel)

### T-005 — Prompts loader + hash audit [P]
**Files**:
- `apps/api/app/prompts/director_v1.system.txt`
- `apps/api/app/prompts/director_v1.user.j2`
- `apps/api/app/domain/director_prompts.py`
- `apps/api/tests/unit/test_director_prompts.py`

**API**:
```python
DIRECTOR_V1_SYSTEM_SHA256: str
DIRECTOR_V1_USER_SHA256: str
def load_system_prompt() -> str: ...
def render_user_prompt(ctx: DirectorContext) -> str: ...
```

### T-006 — Verdicts Pydantic models [P]
**Files**:
- `apps/api/app/domain/director_verdicts.py`
- `apps/api/tests/unit/test_director_verdicts.py`

Mirrors `contracts/director-response.schema.json`. Test asserts schema parity via
`json.dumps(BaseModel.model_json_schema())` ≅ contract file.

### T-007 — Slur list [P]
**Files**:
- `apps/api/app/domain/slur_list.py`
- `apps/api/tests/unit/test_slur_list.py`

**API**: `matches_slur(content: str) -> bool` — compiled regex, case-insensitive.
Curated list (≤ 30). Tests cover positive + negative + boundary (word edges).

### T-008 — DirectorContext + helper repos [P]
**Files**:
- `apps/api/app/domain/director_context.py`
- `apps/api/app/infra/twists_repo.py` (extend with `list_pending_for_chapter`,
  `list_all_for_chapter_for_replay`, `update_status_bulk`)
- tests/integration/

### T-009 — Director filter orchestrator → T-001..T-008
**Files**:
- `apps/api/app/domain/director_filter.py`
- `apps/api/tests/integration/test_director_filter_e2e.py`
- `apps/api/tests/integration/test_director_default_deny.py`
- `apps/api/tests/integration/test_director_fallback.py`
- `apps/api/tests/integration/test_director_all_providers_down.py`

**Behavior**: full pipeline per spec. Uses `FakeLLMProvider` in tests.

---

## Phase 2 — Integration with FSM (1 PR)

### T-010 — DI registration in `main.py` → T-009
**Files**:
- `apps/api/app/main.py` (extend startup)
- `apps/api/tests/integration/test_di_registration.py`

**Behavior**: at startup, after the cycle FSM stub is registered, overwrite
`side_effects.register("director_filter", real_impl)`. Test asserts the resolved
function is the real impl, not the stub.

---

## Phase 3 — Admin replay (2 PRs)

### T-011 — `POST /internal/director/replay` → T-009 [P]
**Files**:
- `apps/api/app/api/internal_director_replay.py`
- `apps/api/tests/integration/test_director_replay_endpoint.py`

**Behavior**: reuses `admin_token` middleware (003). Re-classifies any-status
twists (excluding `deleted_by_user`). Returns breakdown.

### T-012 — `pnpm rerun-filter` CLI → T-011
**Files**:
- `apps/api/app/scripts/rerun_filter.py`
- root + apps/api `package.json` delegation
- integration test

---

## Phase 4 — Live smoke + ops (2 PRs)

### T-013 — Live Gemini test → T-002
**Files**:
- `apps/api/tests/live/test_gemini_smoke.py` (marked `@pytest.mark.live`)
- `.github/workflows/live-llm-smoke.yml` (nightly 02:00 UTC schedule)

**Behavior**: hits real Gemini with a 3-twist synthetic batch; asserts non-empty
verdicts. Requires `GEMINI_API_KEY` repo secret.

### T-014 — Live GH Models test → T-003 [P]
**Files**:
- `apps/api/tests/live/test_github_models_smoke.py`

---

## Phase 5 — Deploy + observe (1 PR)

### T-015 — Prod deploy + observe one filter run → T-010..T-012
**Files**:
- `specs/006-directors-filter/quickstart.md` (verified)
- `specs/README.md` (mark 006 done; 007 in-progress)

**Done when**: a real 18:00 ART run logs the full event sequence and the cycle
reaches `VOTACION` on its own.

---

## Done-when (module-level acceptance)

1. All 15 tasks merged.
2. Every box in [checklists/requirements.md](./checklists/requirements.md) ticked.
3. A production filter run on real data leaves all twists classified (zero
   `pending_review` after the side-effect completes).

---

## Estimates (solo dev, calendar days)

| Phase | Tasks | Est. days |
|---|---|---|
| 0 — LLM provider | T-001..T-004 | 2.5 |
| 1 — Domain | T-005..T-009 | 3 |
| 2 — FSM integration | T-010 | 0.5 |
| 3 — Admin replay | T-011..T-012 | 1.5 |
| 4 — Live smoke | T-013..T-014 | 1 |
| 5 — Deploy | T-015 | 0.5 |
| **Total** | 15 tasks | **≈ 9 days** |

Buffer +25% for first-time Gemini SDK + prompt iteration → **plan for 12 working
days**.
