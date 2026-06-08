# Phase 0 Research: Director's Filter

**Branch**: `006-directors-filter` | **Date**: 2026-06-07

---

## R-001 — LLM provider abstraction shape

**Question**: what's the right interface for an LLM call in our app?

**Decision**: a single method `chat_json(system, user, response_schema, temperature,
max_output_tokens) -> LLMResponse`. Async. Forces JSON-structured output via a
Pydantic schema. No raw-text `chat()` method — every consumer wants structured
data in MVP.

**Rationale**: same logic as the SDD §4.5 `ImageProvider`. A narrow surface is
easier to mock, easier to swap, and matches our real needs.

```python
@dataclass(frozen=True)
class LLMResponse:
    content: BaseModel        # already-parsed structured output
    provider: str             # "gemini" | "github_models" | "fake"
    model: str
    latency_ms: int
    tokens_in: int
    tokens_out: int

class LLMProviderError(Exception): ...
class LLMProviderRateLimited(LLMProviderError): ...
class LLMProviderUnavailable(LLMProviderError): ...
class LLMProviderInvalidOutput(LLMProviderError): ...

class LLMProvider(ABC):
    name: str
    async def health(self) -> bool: ...
    async def chat_json(
        self, *, system: str, user: str,
        response_schema: type[BaseModel],
        temperature: float = 0.2,
        max_output_tokens: int = 2048,
    ) -> LLMResponse: ...
```

**Trigger to revisit**: if streaming becomes valuable (e.g., for a future
realtime moderation use), add `chat_json_stream`. Not in MVP.

---

## R-002 — Model selection: which Gemini / which fallback?

**Question**: which exact model strings do we pin?

**Decision**:

| Provider | Model string | Why |
|---|---|---|
| Gemini primary | `gemini-2.0-flash` | Free tier, JSON-mode mature, low latency |
| GitHub Models fallback | `gpt-4o-mini` | Free for personal use, similar capability tier, OpenAI-compatible SDK |

**Rationale**: matches SDD §2.4. Both are stable in mid-2026.

**Trigger to revisit**: if Gemini deprecates the model or the cost model changes.
The pinned string is in `settings.py` and a one-line change.

---

## R-003 — Prompt versioning strategy

**Question**: where do prompts live?

| Option | Pros | Cons |
|---|---|---|
| Inline Python strings | Co-located with code | Long strings hurt readability; no clean diff |
| `prompts/director_v1.system.txt` + Jinja2 user template (chosen) | Editable in non-code PRs; diff-friendly; can be version-bumped independently | Two files to load at startup |
| DB-stored prompts with admin UI | Hot reload, no deploy | Heavy infra for MVP |

**Decision**: **files**. The version goes in the filename (`director_v1.system.txt`,
`director_v1.user.j2`). Bumping the version is a git operation. Tests pin to a
specific version. Hot reload is not a feature we need.

**Hash audit**: `tests/unit/test_director_prompts.py::test_prompt_hashes_match`
computes sha256 of each prompt file and compares to the constant in
`director_prompts.py`. Any prompt edit forces a constant update — surfaces drift.

---

## R-004 — Default-deny semantics

**Question**: an LLM returns 23 verdicts when 25 twists were sent. What do we do
with the missing 2?

| Option | Behavior | Risk |
|---|---|---|
| Default-approve | Treat missing as approved | LLM silently letting noise through |
| **Default-deny (chosen)** | Treat missing as `rejected_incoherent` | Conservative; a coherent twist might get unfairly rejected |
| Re-prompt only the missing | Costs another LLM call | Bounded recursion; complexity |

**Decision**: **default-deny**. Reason text:
`"No clasificado por el filtro (fail-closed)."`. A user whose twist was missed
sees a rejection and a clear reason; the PO can manually resurface it via the
admin replay endpoint.

**Rationale**: false negatives (missing-approval) are recoverable (re-run filter);
false positives that leak through pollute the vote feed and lose user trust.

---

## R-005 — Slur post-filter

**Question**: do we trust the LLM as the only line of defense against offensive
content?

**Decision**: **no**. Post-filter every `approved` verdict against a curated
Spanish slur regex list (`app/domain/slur_list.py`). Match → override to
`rejected_offensive`.

**Why**:

1. **Defense in depth** (Constitution Gate 9): LLM outputs are untrusted.
2. **Prompt injection resistance**: a twist that successfully convinces the LLM
   to mark it `approved` despite containing a slur is still rejected.
3. **Cheap**: regex match over ≤ 280 chars is sub-millisecond.

**Curation policy**: ~30 entries to start, in `slur_list.py` constant. Patches
require code review (no DB / no hot reload). The list is intentionally
conservative — context-sensitive slurs (e.g., reclaimed terms in some communities)
are excluded; that responsibility lies with the LLM.

**Out of scope**: multi-language lists; ML-based classifier; self-update from
external datasets.

---

## R-006 — Free-tier budget tracking

**Question**: do we need a DB table to count LLM calls?

**Decision**: **no, structured logs**. Every call emits `llm_batch
{provider, tokens_in, tokens_out, latency_ms}`. A daily Fly log aggregation can
compute the budget if we ever need to. For MVP, expected daily volume (≤ 10
batches) is so far below the limit (1500 RPD) that the risk is negligible.

A `llm_budget_warn` log fires at ≥ 70 % of the daily quota; the threshold lives
in `settings.py` and tracks against an in-process counter (best-effort; resets on
machine restart, which is acceptable since we deploy infrequently).

**Trigger to revisit**: if usage approaches 50 % of free tier sustained.

---

## R-007 — Live tests against the real LLM

**Question**: do we hit the real Gemini in CI?

**Decision**: **no, except an opt-in nightly job**. CI uses `FakeLLMProvider`
which is deterministic. A separate marker `@pytest.mark.live` guards real-API
tests; CI workflow has an optional `live-llm-smoke.yml` triggered on a 02:00 UTC
schedule that runs a single 3-twist batch against Gemini and asserts the response
is non-empty. Cheap, catches API breakage between deploys.

**Why not on every PR**: free tier quota is shared; flaky CI from rate limits
would erode confidence.

---

## R-008 — PII concerns in twist content

**Question**: twist content goes to Gemini and GitHub Models. Is that OK?

**Decision**: **yes for MVP**, with documented user consent.

**Reasoning**:

- The PWA's onboarding screen displays a note: *"Tus ideas se procesan con un
  modelo de IA para moderarlas. No incluyas datos personales (DNI, teléfono,
  dirección)."*
- The free-tier ToS of both providers allow this use.
- For closed beta of family/friends, this is acceptable.

**Trigger to revisit**: public launch. Evaluate self-hosted moderation
(transformers via HF on GPU local — module 009 path).

---

## R-009 — Batch size

**Question**: 25 is the SDD default. Why not 50? Or 10?

**Decision**: **25**. Empirically:

- Token budget: 25 twists × ~50 tokens (avg) + bible + cliffhanger context
  ≈ 2 500 tokens in — well under Gemini 1M input window. The bottleneck is
  output token budget (each verdict ≈ 30 tokens; 25 verdicts ≈ 750 output
  tokens, within our 2048 cap).
- Latency: 25 fits in one Gemini call ≤ 5 s p50; larger batches risk
  schema-validation failure if Gemini truncates.
- Default-deny risk: smaller batches mean a missed verdict is "expensive" (fewer
  total twists per call); 25 is a reasonable balance.

**Configurable** via `DIRECTOR_BATCH_SIZE` env. Tests use 5 to keep fixtures
manageable.

---

## R-010 — Replay endpoint vs FSM transition

**Question**: should `/internal/director/replay` reuse the FSM
`/internal/transition` plumbing?

**Decision**: **no, dedicated endpoint**. The transition endpoint expects an HMAC
tick. The replay is a manual ops action triggered by the PO via `pnpm rerun-filter`.
Different auth (`ADMIN_TOKEN`), different semantics (re-classify all twists,
including already-classified ones), and it does NOT change FSM state.

**Trigger to revisit**: if a future "auto-rerun on detection of anomaly" feature
ships, that could route through the FSM. Not in MVP.

---

## Open items

- **OQ-DF-1**: per-user moderation history visible to the user (transparency).
  Defer; no module currently surfaces rejections beyond `/me/twists` (module 005).
- **OQ-DF-2**: rate-limit the admin replay endpoint? Trivial endpoint, ADMIN_TOKEN-
  protected; defer.
- **OQ-DF-3**: should `director_reason` be ML-generated AND human-overridable?
  Defer to an admin panel module if/when needed.
