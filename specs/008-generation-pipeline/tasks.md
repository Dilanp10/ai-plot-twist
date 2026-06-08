# Task Breakdown: Generation Pipeline

**Branch**: `008-generation-pipeline` | **Date**: 2026-06-07

---

## Phase 0 — Pure domain (5 PRs, parallel)

### T-001 — Winner selector [P]
**Files**:
- `apps/api/app/domain/winner_selector.py`
- `apps/api/tests/unit/test_winner_selector.py`

**API**:
```python
@dataclass(frozen=True)
class WinnerPick:
    winner_twist_id: int | None
    winner_public_id: UUID | None
    winner_user_display_name: str | None
    vote_count: int
    tiebreak: bool
    runner_up_twist_id: UUID | None

async def pick_winner(session, chapter_id: int) -> WinnerPick: ...
```

Tests: clear winner, 2-way tie, 3-way tie, 0 rows.

### T-002 — Seed derivation [P]
**Files**:
- `apps/api/app/domain/seed_derivation.py`
- `apps/api/tests/unit/test_seed_derivation.py`

**API**: `def stable_hash(chapter_id: int, panel_idx: int) -> int` (32-bit
positive int). Same input → same output.

### T-003 — `ScriptwriterResponse` Pydantic [P]
**Files**:
- `apps/api/app/domain/scriptwriter_response.py`
- `apps/api/tests/unit/test_scriptwriter_response.py`

Mirrors `contracts/scriptwriter-response.schema.json`. Includes the
visual_prompt-English validator (FR research R-002).

### T-004 — Scriptwriter prompts (files + loader + hash audit) [P]
**Files**:
- `apps/api/app/prompts/scriptwriter_v1.system.txt`
- `apps/api/app/prompts/scriptwriter_v1_auto.system.txt`
- `apps/api/app/prompts/scriptwriter_v1.user.j2`
- `apps/api/app/domain/scriptwriter_prompts.py`
- `apps/api/tests/unit/test_scriptwriter_prompts.py`

### T-005 — Manifest builder [P]
**Files**:
- `apps/api/app/domain/manifest_builder.py`
- `apps/api/tests/unit/test_manifest_builder.py`

**API**: pure functions that take pipeline state and produce the manifest_json
shape per `contracts/manifest-shape.md`. Includes the
`schema_version="1.0"` constant.

---

## Phase 1 — Infra (2 PRs)

### T-006 — R2 uploader → 001-merged [P]
**Files**:
- `apps/api/app/infra/r2_uploader.py`
- `apps/api/tests/unit/test_r2_uploader.py` (mock-based)
- `apps/api/scripts/upload_static_assets.py` (+ `assets/placeholder.webp`)

**API**:
```python
class R2Uploader:
    def __init__(self, account_id, key_id, secret, bucket, public_base_url): ...
    async def upload(self, key: str, body: bytes, content_type: str) -> str:
        """Returns public URL."""
```

Uses `boto3` wrapped in `run_in_executor`. 3 retries on 5xx.

### T-007 — TTS synthesizer → 001-merged [P]
**Files**:
- `apps/api/app/domain/tts_synthesizer.py`
- `apps/api/tests/unit/test_tts_synthesizer.py`

**API**:
```python
async def synthesize(text: str, voice: str = "es-AR-ElenaNeural") -> bytes | None:
    """Returns MP3 bytes or None on any failure. Never raises."""
```

Wraps the `edge-tts` library.

---

## Phase 2 — Consumers of providers (2 PRs)

### T-008 — Scriptwriter consumer → T-003, T-004, 006-merged
**Files**:
- `apps/api/app/domain/scriptwriter.py`
- `apps/api/tests/integration/test_scriptwriter_with_fake_llm.py`

**API**:
```python
class Scriptwriter:
    def __init__(self, llm_router: LLMProviderRouter): ...
    async def draft(self, context: ScriptContext) -> ScriptwriterResponse: ...
```

Auto-continue branch chooses the auto system prompt.

### T-009 — Panel pipeline → T-002, T-006, T-007, 009-merged
**Files**:
- `apps/api/app/domain/panel_pipeline.py`
- `apps/api/tests/integration/test_panel_pipeline.py`

**API**:
```python
@dataclass
class PanelResult:
    idx: int
    image_url: str
    image_blurhash: str | None
    tts_url: str | None
    provider_used: str        # "pollinations"|"hf"|"placeholder"
    ok: bool                   # False if placeholder

async def render_panel(
    *, panel: Panel, chapter_id: int, chapter_public_id: UUID,
    season_slug: str, image_router, uploader, tts_voice,
    placeholder_url: str,
) -> PanelResult: ...
```

Per-panel: render → TTS (best-effort) → upload bytes → upload TTS → return.

---

## Phase 3 — Coordinator (1 PR)

### T-010 — Generation pipeline coordinator → T-001, T-005, T-008, T-009
**Files**:
- `apps/api/app/domain/generation_pipeline.py`
- `apps/api/tests/integration/test_generation_happy.py`
- `apps/api/tests/integration/test_generation_tie.py`
- `apps/api/tests/integration/test_generation_no_winner.py`
- `apps/api/tests/integration/test_generation_panel_failure.py`
- `apps/api/tests/integration/test_generation_scriptwriter_failure.py`
- `apps/api/tests/integration/test_generation_deadline.py`
- `apps/api/tests/integration/test_generation_r2_failure.py`

**Body**: orchestrator per spec FR-001..FR-011. Includes the deadline
race pattern from research R-008.

---

## Phase 4 — Integration with FSM (1 PR)

### T-011 — DI registration → T-010
**Files**:
- `apps/api/app/main.py` (startup)
- `apps/api/tests/integration/test_di_generation_registration.py`

Asserts `side_effects.get("generation_pipeline")` returns the real impl.

---

## Phase 5 — Admin replay (2 PRs)

### T-012 — `POST /internal/generation/rerun` → T-010 [P]
**Files**:
- `apps/api/app/api/internal_generation_rerun.py`
- `apps/api/tests/integration/test_generation_rerun_endpoint.py`

### T-013 — `pnpm rerun-generation` CLI → T-012
**Files**:
- `apps/api/app/scripts/rerun_generation.py`
- Root + apps/api `package.json` delegation
- integration test

---

## Phase 6 — Live + deploy (2 PRs)

### T-014 — Live full pipeline smoke → T-010
**Files**:
- `apps/api/tests/live/test_full_pipeline_smoke.py` (marked live)
- `.github/workflows/live-llm-smoke.yml` (extend to include this)

### T-015 — Deploy + observe one calendar cycle → T-011..T-014
**Files**:
- `specs/008-generation-pipeline/quickstart.md` (verified)
- `docs/adr/0004-scriptwriter-creativity-exception.md` (ADR for Gate 5)
- `specs/README.md` (mark 008 done; 010 in-progress)

**Done when**: a real 23:00 ART run on Fly produces a real `ready` chapter the
following 12:00 ART without manual intervention.

---

## Done-when (module-level acceptance)

1. All 15 tasks merged.
2. Every box in [checklists/requirements.md](./checklists/requirements.md) ticked.
3. A 7-day window on Fly shows 7 chapters generated end-to-end with zero PO
   intervention (the bar for the closed-beta launch).

---

## Estimates (solo dev, calendar days)

| Phase | Tasks | Est. days |
|---|---|---|
| 0 — Pure domain | T-001..T-005 | 3 |
| 1 — Infra | T-006..T-007 | 2 |
| 2 — Consumers | T-008..T-009 | 2.5 |
| 3 — Coordinator | T-010 | 3 |
| 4 — FSM integration | T-011 | 0.5 |
| 5 — Admin replay | T-012..T-013 | 1.5 |
| 6 — Live + deploy | T-014..T-015 | 2 |
| **Total** | 15 tasks | **≈ 14.5 days** |

Buffer +35% for first-time R2 + Edge-TTS + iteration on the scriptwriter prompt
→ **plan for 20 working days**.
