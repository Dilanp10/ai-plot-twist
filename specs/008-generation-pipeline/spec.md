# Feature Specification: Nightly Generation Pipeline

**Feature Branch**: `008-generation-pipeline`
**Created**: 2026-06-07
**Status**: Draft
**Depends on**: `003-cycle-fsm`, `006-directors-filter`, `007-voting`,
                `009-image-providers`

## Summary

Replace the `generation_pipeline_stub` registered by module 003 with the real
nightly pipeline described in SDD §2.3.4 and §4.3. At the 23:00 ART tick, the FSM
enters `GENERACION` and spawns a background task that:

1. **Picks the winner** via a deterministic SQL query with the SDD tiebreak rule
   (`votes DESC, submitted_at ASC, id ASC`). If no `approved` twists exist,
   `winner_twist = NULL` and the scriptwriter runs in **auto-continue mode**
   (Ronda 1 decision #10).
2. **Drafts the script** by calling the `LLMProvider` (from module 006) with a
   versioned scriptwriter prompt, producing a structured manifest:
   `{title, synopsis, panels[{idx, narration, visual_prompt, mood, tts_text}],
   cliffhanger, next_cliffhanger_seed}`.
3. **Renders each panel** in parallel via `ImageProviderRouter` (from module 009),
   uploading bytes to Cloudflare R2 at the path scheme of module 009.
4. **Synthesizes TTS** (optional, per panel) via `edge-tts` and uploads.
5. **Persists** a new `chapters` row with `status='ready'` (or `'ready_degraded'`
   on partial failure) and updates `cycles.next_chapter_id`.
6. **Transitions** the cycle to `PENDING_RELEASE` so module 003's ESTRENO cron at
   12:00 ART next day can publish it.

The whole pipeline runs inside the 13-hour window 23:00 → 12:00 next day, with a
`PIPELINE_HARD_DEADLINE_S = 3300` (55 min) soft deadline before the chapter is
marked degraded.

## User Scenarios & Testing

### User Story 1 — Generation produces the next chapter from a winner (Priority: P1)

The 23:00 ART tick fires. By 23:50 the next chapter is `status='ready'` in DB,
its panels live in R2, and `cycles.next_chapter_id` points to it.

**Why this priority**: this is the second core loop of the product (filter →
vote → **generate**). Without it, day N+1 has nothing to show.

**Independent Test**: bootstrap a cycle, populate twists, run filter, cast
votes, force `GENERACION`, wait for the pipeline, assert a new chapter row +
panels reachable at their R2 URLs.

**Acceptance Scenarios**:

1. **Given** the chapter has 5 approved twists with votes (1 winner clear),
   **When** the 23:00 transition fires,
   **Then** within `PIPELINE_HARD_DEADLINE_S` seconds: (a) the winner is the
   twist with most votes; (b) `chapters` has a new row with `day_index = N+1`,
   `status='ready'`; (c) `manifest_json.panels` has 3–4 entries with public R2
   URLs that return 200; (d) `cycles.next_chapter_id` points to the new chapter;
   (e) cycle is in `PENDING_RELEASE`.

2. **Given** two twists tied on `vote_count`,
   **When** the winner is picked,
   **Then** the one with the earlier `submitted_at` wins; if still tied, the one
   with the lower internal `id` wins. The losing twist is stored in
   `manifest_json.winner_metadata.runner_up_twist_id` for transparency (see
   SDD UC-3).

### User Story 2 — Auto-continue when no twists approved (Priority: P1)

Per Ronda 1 #10, if no approved twists exist the scriptwriter continues the plot
autonomously from the cliffhanger seed.

**Acceptance Scenarios**:

1. **Given** 0 approved twists exist for the chapter,
   **When** the generation pipeline runs,
   **Then** the scriptwriter is invoked with `winner_twist=None` and an extended
   system prompt instructing autonomous continuation (per SDD §4.3 "Caso
   degenerado"). The resulting chapter is `status='ready'` (NOT
   `ready_degraded`). Log emits `cycle_autocontinued {cycle_id, chapter_id}`.

### User Story 3 — Partial panel failure → `ready_degraded` (Priority: P1)

Panel 3 of 4 fails all image retries on both providers; the chapter still ships
on time with a placeholder URL for that panel.

**Why this priority**: the loop must not break for a single asset failure.

**Acceptance Scenarios**:

1. **Given** panels 1, 2, 4 render successfully but panel 3 exhausts the
   `ImageProviderRouter`,
   **When** the pipeline finalizes,
   **Then** the chapter row is `status='ready_degraded'`, the failed panel's
   `image_url` is the project's static placeholder
   (`https://assets.aiplottwist.example/static/placeholder.webp`), the cycle
   still transitions to `PENDING_RELEASE`, AND a Discord webhook alert fires
   with the cycle + chapter ids and the failure summary.

### User Story 4 — Scriptwriter LLM failure → cycle FAILED (Priority: P1)

The scriptwriter is the single point of failure that **cannot** be partially
recovered (no script → no panels).

**Acceptance Scenarios**:

1. **Given** both LLM providers exhaust their retries during the scriptwriter
   call,
   **When** the failure surfaces,
   **Then** module 003's `safe_side_effect` catches it: cycle transitions to
   `FAILED`, kill-switch auto-on, Discord alert. No partial chapter is created.

### User Story 5 — Pipeline deadline → mark degraded but ship (Priority: P2)

The pipeline starts at 23:00 and is still working at 23:55. The watchdog (module
003) inspects, the pipeline coordinator detects deadline exceeded.

**Acceptance Scenarios**:

1. **Given** elapsed `> PIPELINE_HARD_DEADLINE_S` and the chapter is not yet
   finalized,
   **When** the coordinator detects the deadline,
   **Then** it stops new panel work, fills missing panels with the placeholder,
   marks `status='ready_degraded'`, transitions the cycle to `PENDING_RELEASE`,
   alerts Discord.

### User Story 6 — Admin can re-run generation (Priority: P2)

The PO sees a bad output, fixes via `pnpm rerun-generation --chapter-id N+1`.

**Acceptance Scenarios**:

1. **Given** the next chapter row exists and has `status IN ('ready',
   'ready_degraded')`,
   **When** the PO runs `pnpm rerun-generation`,
   **Then** the existing chapter's `manifest_json` is replaced with a fresh
   generation; assets re-uploaded to NEW R2 paths (content-hash changes); the
   chapter row's `released_at` is bumped to invalidate caches (per module 004
   research R-005); status restored to `ready`.

### Edge Cases

- **Idempotent re-fire of 23:00 tick**: blocked by module 003's UNIQUE
  `(cycle, to_state, trigger_id)`.
- **Concurrent pipeline runs for the same chapter**: prevented by the FSM
  advisory lock (003). The pipeline does NOT acquire its own lock.
- **Pipeline crashes mid-flight**: `safe_side_effect` catches at the outer layer;
  cycle to FAILED. Partial uploaded assets remain in R2 (not cleaned). PO must
  rerun.
- **Scriptwriter returns a panel count outside [3, 4]**: prompt and Pydantic
  schema enforce `3 ≤ len(panels) ≤ 4`; on schema-violation, retry; on second
  failure, log and use the closest valid count by truncation/padding.
- **R2 upload returns 5xx**: retry up to 3 times with backoff; on exhaustion,
  use placeholder for that panel.
- **TTS fails**: optional; skip TTS for that panel; do NOT mark degraded just
  for TTS.
- **R2 credentials missing**: pipeline raises at first upload; outer wrapper to
  FAILED.
- **Two days bootstrapped same cycle_date** (operator mistake): impossible,
  enforced by `UNIQUE(season_id, cycle_date)` (module 003).
- **Winner twist's content has changed since vote** (user deleted it): the
  `votes` rows still exist but the twist is `deleted_by_user`. Excluded from
  selection (the SQL filters by `status='approved'`); next-best winner is
  selected; no error.

## Requirements

### Functional Requirements

- **FR-001**: Pipeline replaces `generation_pipeline_stub` via DI at FastAPI
  startup. Same async signature: `async def generation_pipeline(chapter_id:
  int) -> None`. Module 003's executor is unchanged.
- **FR-002**: **Step 1 — Pick winner.** Run the SDD §4.3 query verbatim. If
  result is empty, set `winner_twist=None` and proceed in auto-continue mode.
  In all other cases, persist `winner_metadata` in the new chapter's
  `manifest_json` for transparency:
  ```json
  {
    "winner_twist_id": "<uuid>",
    "winner_author_public_id": "<uuid>",
    "winner_author_display_name": "...",
    "vote_count": 12,
    "tiebreak": false,
    "runner_up_twist_id": null
  }
  ```
  On tie (≥ 2 twists share top vote_count), `tiebreak=true` and `runner_up_twist_id`
  is the deterministic-second.
- **FR-003**: **Step 2 — Draft script.** Call `LLMProvider.chat_json` with the
  versioned scriptwriter prompt (`prompts/scriptwriter_v1.system.txt` +
  `scriptwriter_v1.user.j2`) and the Pydantic response model
  `ScriptwriterResponse`. `temperature=0.6` (more creative than the filter).
  `max_output_tokens=4096`. The auto-continue mode uses a different system prompt
  variant (`scriptwriter_v1_auto.system.txt`).
- **FR-004**: **Step 3 — Render panels.** For each panel, compose the T2I prompt
  per SDD §4.4 (visual_prompt + style.global_tags + style.negative_hint), derive
  seed `seed = stable_hash(chapter_id, panel.idx)`, call
  `ImageProviderRouter.render`. Up to `PANEL_CONCURRENCY` (default 4) panels in
  parallel via `asyncio.gather(return_exceptions=True)`.
- **FR-005**: **Step 4 — TTS (optional).** If `TTS_ENABLED=true` (default), call
  `edge-tts` with voice `TTS_VOICE` (default `es-AR-ElenaNeural`), stream the
  MP3 bytes, upload to R2 at `seasons/{slug}/{chapter_public_id}/{panel_idx}-
  tts-{sha256(bytes)[:8]}.mp3`. TTS failure is logged but does NOT block panel
  completion or degrade the chapter.
- **FR-006**: **Step 5 — Upload to R2.** Use the `boto3` S3-compatible client
  pointed at R2 (`{R2_ACCOUNT_ID}.r2.cloudflarestorage.com`). Key from
  `compute_r2_path` (module 009). Retry 3× on 5xx with backoff `[1, 3, 9]`.
  Content-Type set per asset. Cache-Control header `public, max-age=31536000,
  immutable`.
- **FR-007**: **Step 6 — Persist.** Insert the new `chapters` row in ONE
  transaction with the final `manifest_json` (all panel URLs filled). Update
  `cycles.next_chapter_id` to the new chapter id in the same transaction.
- **FR-008**: **Step 7 — Transition.** Call `cycle_executor.transition` to
  `PENDING_RELEASE` with `triggered_by='side_effect'`,
  `trigger_id=f"generation-{chapter_id}-{uuid()}"`.
- **FR-009**: **Deadline coordinator.** Pipeline starts a wall-clock timer at
  entry; every 30 s checks elapsed. On `elapsed > PIPELINE_HARD_DEADLINE_S`, the
  coordinator cancels outstanding panel tasks, fills missing panels with the
  placeholder URL, sets status to `ready_degraded`, posts Discord alert,
  transitions cycle.
- **FR-010**: **Partial failure handling.** If `asyncio.gather(...,
  return_exceptions=True)` returns N panel exceptions where N < total: the
  successful panels keep their URLs; failed panels get the placeholder URL;
  chapter status is `ready_degraded`; Discord alert fires.
- **FR-011**: **All-panels-failed**: not specially handled — even if every
  panel falls back to placeholder, the chapter is `ready_degraded`. The PO can
  call `pnpm rerun-generation` to retry.
- **FR-012**: **Scriptwriter prompt files** under `app/prompts/`:
  - `scriptwriter_v1.system.txt`
  - `scriptwriter_v1_auto.system.txt` (auto-continue variant)
  - `scriptwriter_v1.user.j2`
  Hash audit identical to module 006 R-003.
- **FR-013**: **`POST /api/v1/internal/generation/rerun`** (admin-auth) accepts
  `{chapter_id}`, runs the pipeline against the specified chapter (must be the
  current `next_chapter`), replaces the existing manifest, bumps `released_at`,
  does NOT touch cycle state.
- **FR-014**: **`pnpm rerun-generation --chapter-id <uuid>`** CLI wraps the
  endpoint.
- **FR-015**: Structured log events:
  - `generation_started {chapter_id, has_winner, twist_count}`.
  - `winner_picked {twist_id, vote_count, tiebreak, runner_up?}`.
  - `scriptwriter_done {model, panels, latency_ms, tokens_in, tokens_out}`.
  - `panel_render_started {panel_idx, seed}`.
  - `panel_render_done {panel_idx, provider, model, latency_ms, ok}`.
  - `tts_done {panel_idx, ok, latency_ms}`.
  - `r2_upload_done {key, content_length, ok}`.
  - `generation_completed {chapter_id, status, duration_ms, panels_ok, panels_degraded}`.
  - `generation_deadline_exceeded {chapter_id, elapsed_s}` (if applicable).
- **FR-016**: **Settings**:
  - `SCRIPTWRITER_TEMPERATURE` (default 0.6).
  - `SCRIPTWRITER_MAX_OUTPUT_TOKENS` (default 4096).
  - `PANEL_CONCURRENCY` (default 4).
  - `PIPELINE_HARD_DEADLINE_S` (default 3300).
  - `TTS_ENABLED` (default true), `TTS_VOICE` (default `es-AR-ElenaNeural`).
  - `PLACEHOLDER_IMAGE_URL` (default
    `https://assets.aiplottwist.example/static/placeholder.webp`).
  - `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`,
    `R2_PUBLIC_BASE_URL`.

### Non-Functional Requirements

- **NFR-001**: End-to-end pipeline p95 ≤ 50 min on real providers under
  expected free-tier latency (Pollinations ~10 s/panel, Gemini ~5 s for script).
- **NFR-002**: With FakeLLM + FakeImage (latency 100 ms each), pipeline
  completes in ≤ 5 s (CI assertion).
- **NFR-003**: R2 upload p95 < 2 s per panel for typical (~500 KB) webp.
- **NFR-004**: Memory ≤ 200 MB on Fly machine (256 MB limit). Streaming
  uploads, no full-pipeline buffering.

### Out of Scope (for this feature)

- Real video generation. SDD §1.4 NG-1.
- Multi-image-per-panel variants (one image per panel).
- Custom voice per character. Single TTS voice; future improvement.
- Caching layer for repeated scriptwriter calls. Each generation is fresh.
- Cleanup of orphaned R2 objects after rerun-generation. Documented as cost-
  zero acceptable (R2 storage is free up to 10 GB).
- Concurrent generation for multiple seasons (single active season in MVP).
- Per-panel LoRA / IP-Adapter for character consistency (SDD §8 R-3).
