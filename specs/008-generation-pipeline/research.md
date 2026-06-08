# Phase 0 Research: Nightly Generation Pipeline

**Branch**: `008-generation-pipeline` | **Date**: 2026-06-07

---

## R-001 — Winner-selection query and tiebreak

**Question**: implement the SDD §4.3 query verbatim?

**Decision**: **yes**. The SDD has a clear, reasoned tiebreak rule (`votes
DESC, submitted_at ASC, id ASC`); we don't reinvent it. Captured exactly in
`winner_selector.py` with a single function `pick_winner(chapter_id, session)
-> WinnerOrNone`. Unit-tested with synthetic data covering:
- Clear winner (1 leader).
- Two-way tie broken by `submitted_at`.
- Three-way tie broken by `id`.
- Zero approved twists (returns None).

**Important**: the query MUST also expose the **runner-up** id for transparency
in the manifest (FR-002). Use `ROW_NUMBER() OVER (ORDER BY votes DESC,
submitted_at ASC, id ASC)` and take the top 2 rows.

---

## R-002 — Visual prompt language

**Question**: the scriptwriter produces `visual_prompt` strings that go to
Pollinations/HF. Should they be in English (better T2I quality) or Spanish
(matches narrative voice)?

**Decision**: **English**, enforced by the scriptwriter prompt.

**Rationale**: T2I models are overwhelmingly trained on English captions.
Spanish prompts produce lower-fidelity images. Narrative text (narration,
cliffhanger, tts_text) stays in Spanish; only `visual_prompt` is English.

**Enforcement**:
1. System prompt: "El `visual_prompt` de cada panel debe estar en INGLÉS y
   optimizado para diffusion models."
2. Pydantic validator: `visual_prompt` must be > 80 % ASCII printable
   characters (catches accidental Spanish).
3. Test fixture asserts an example prompt is English.

---

## R-003 — Auto-continue: separate prompt vs flag

**Question**: when `winner_twist=None`, do we pass a flag to the same prompt or
load a different system prompt?

**Decision**: **separate file** — `scriptwriter_v1_auto.system.txt`.

**Rationale**: the two cases have different framing:

- **With winner**: "Continuá la trama incorporando esta propuesta como giro:
  '<winner.content>'."
- **Auto-continue**: "Continuá la trama de manera coherente con la bible y el
  cliffhanger. No menciones que no hubo propuestas; narrá normal."

A single prompt with a conditional Jinja section is harder to maintain and
review. Two files with their own hash-pinned constants make each path
inspectable.

---

## R-004 — Parallel vs serial panel rendering

**Question**: render all panels in parallel or one at a time?

| Option | Pros | Cons |
|---|---|---|
| **Parallel with semaphore (chosen)** | Walltime ≈ max(panel_latency); fits deadline | Burst load on free-tier providers |
| Serial | Predictable; simple | 4× walltime; tight deadline |

**Decision**: **parallel**, bounded by `asyncio.Semaphore(PANEL_CONCURRENCY)`
(default 4). For 4 panels and `PANEL_CONCURRENCY=4`, they all start at once.

**Burst-load concern**: free-tier providers may rate-limit if we hit them too
fast. With 4 parallel calls to Pollinations, this is acceptable in MVP; if we
observe 429s, drop concurrency to 2.

**Configurable**: `PANEL_CONCURRENCY` env var.

---

## R-005 — Scriptwriter determinism

**Question**: Gate 5 requires determinism in critical paths. But creativity
requires `temperature > 0`. How do we square this?

**Decision**: **explicit non-determinism exception**, documented here.

**Rationale**: the scriptwriter is intentionally non-deterministic — the same
winner + the same bible should NOT produce the same chapter every time, because
that's bad fiction. Determinism in this module applies to:

- Winner selection (yes, deterministic).
- T2I seed (yes, deterministic).
- R2 path computation (yes, deterministic — module 009).
- Scriptwriter LLM call (NO, intentionally).

The constitution Gate 5 footnote in `docs/adr/0004-scriptwriter-creativity-
exception.md` codifies this.

**Temperature choice**: `0.6`. Empirically: 0.2 produces wooden prose, 1.0
produces incoherent jumps, 0.6 is the sweet spot reported across most
narrative LLM tooling in 2026.

---

## R-006 — Pydantic schema for `ScriptwriterResponse`

**Decision**:

```python
class Panel(BaseModel):
    idx: int = Field(..., ge=1, le=8)
    narration: str = Field(..., min_length=10, max_length=500)
    visual_prompt: str = Field(..., min_length=20, max_length=400)
    mood: Literal["tense","ominous","contemplative","hopeful","absurd",
                  "melancholic","euphoric","dread","tender"]
    tts_text: str = Field(..., min_length=10, max_length=500)

class WinnerMetadata(BaseModel):
    winner_twist_id: UUID | None      # None in auto-continue
    winner_author_display_name: str | None
    tiebreak: bool = False
    runner_up_twist_id: UUID | None = None

class ScriptwriterResponse(BaseModel):
    title: str = Field(..., min_length=5, max_length=80)
    synopsis: str = Field(..., min_length=20, max_length=400)
    panels: list[Panel] = Field(..., min_length=3, max_length=4)
    cliffhanger: str = Field(..., min_length=10, max_length=300)
    next_cliffhanger_seed: str = Field(..., min_length=10, max_length=300)
```

Panel `idx` is provided by the LLM; the persistence step asserts uniqueness and
contiguity `[1..N]`. If violated, retry the call (one retry); on second failure,
renumber server-side.

---

## R-007 — `boto3` vs `aioboto3`

**Question**: `boto3` is sync; in an async pipeline, does it block the event
loop?

**Decision**: **use `boto3` wrapped in `loop.run_in_executor`**.

**Rationale**: `aioboto3` is technically async but its async story is
inconsistent (some operations are not truly non-blocking). `boto3` is mature,
battle-tested, and the upload sizes are small (~500 KB to 2 MB); wrapping in a
`ThreadPoolExecutor` per upload is a few-line pattern that doesn't hurt
throughput at our scale:

```python
async def upload(self, key: str, body: bytes, content_type: str) -> None:
    await asyncio.get_running_loop().run_in_executor(
        None,  # default executor
        partial(self._client.put_object,
                Bucket=self.bucket, Key=key, Body=body,
                ContentType=content_type,
                CacheControl="public, max-age=31536000, immutable"),
    )
```

**Trigger to revisit**: if uploads become bottleneck. Not expected at ~4
uploads/day.

---

## R-008 — Deadline watcher

**Question**: how to implement the soft deadline that converts to
`ready_degraded` at 55 min?

**Decision**: two `asyncio.Task`s racing:

```python
pipeline_task = asyncio.create_task(_run_pipeline(...))
deadline_task = asyncio.create_task(_wait_deadline(...))

done, pending = await asyncio.wait(
    {pipeline_task, deadline_task},
    return_when=asyncio.FIRST_COMPLETED,
)

if deadline_task in done:
    pipeline_task.cancel()
    await _finalize_degraded(...)   # placeholder panels, ready_degraded
else:
    deadline_task.cancel()
    # normal finalize
```

The pipeline coordinator is the **single writer** to DB. The deadline watcher
only cancels in-flight work and triggers the degraded finalization path.

**Cancellation propagates**: panel tasks use `asyncio.CancelledError` to clean
up partial work; uploaded bytes that were already written stay in R2 (harmless).

---

## R-009 — TTS fire-and-forget semantics

**Question**: if TTS fails for a panel, does the panel fail?

**Decision**: **no**. TTS is optional UX. Panel completion criterion: image
URL exists. TTS URL is nullable in the manifest.

**Implementation**: TTS runs in `try/except`; on failure, logs `tts_done
{ok:false, error_class}`, the panel's `tts_url` is None.

This keeps the chapter status `ready` even when all TTS calls fail, which is
the correct UX — silent panels are fine, missing panels are not.

---

## R-010 — Atomic vs incremental persistence

**Question**: write `chapters.manifest_json` incrementally as panels complete,
or only at the end?

**Decision**: **atomic at end**.

**Pros of atomic**: no half-baked chapter visible to readers (module 004
reads `status='live'` only, but defense-in-depth is good); single transaction
simplifies rollback; ETag (module 004) stays correct.

**Pros of incremental** (rejected): better recovery on crash mid-pipeline.

We chose atomic because the recovery path is "PO runs `pnpm rerun-generation`"
— having a partial chapter row in DB doesn't help; it just confuses the rerun
logic. The pipeline runs in ≤ 55 min; a crash is the FAILED path anyway.

---

## R-011 — Rerun semantics + cache invalidation

**Question**: when rerun-generation overwrites the manifest, do caches in
module 004 reflect the new content?

**Decision**: bump `chapters.released_at = now()` as part of rerun.

**Rationale**: module 004's ETag (research R-005 of that module) is derived
from `(public_id, cycle.state, released_at)`. Touching `released_at`
invalidates all caches at all layers. The original `released_at` (when the
chapter went live) is preserved in logs; the canonical column reflects the
last content version.

---

## R-012 — Manifest schema versioning

**Decision**: include `manifest_json.schema_version: "1.0"` on every new chapter.
Future migrations can branch on this.

---

## R-013 — Placeholder image

**Question**: where does the placeholder image come from?

**Decision**: a static `placeholder.webp` uploaded **once** to R2 at
`static/placeholder.webp` by `scripts/upload_static_assets.py` (one-shot,
included in module 008 deployment). The URL is hardcoded in
`settings.PLACEHOLDER_IMAGE_URL`.

The image: a stylized "..." card with the project's wordmark, no text.

---

## Open items

- **OQ-G-1**: per-chapter "remix" feature (let users propose visual tweaks).
  Out of MVP.
- **OQ-G-2**: chapter regeneration with a different scriptwriter prompt (e.g.,
  "more humor"). PO toggles via env for an entire run; per-chapter variation
  is out of MVP.
- **OQ-G-3**: orphan R2 asset cleanup. Deferred until storage usage warrants.
