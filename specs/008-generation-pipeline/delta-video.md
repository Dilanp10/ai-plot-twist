# Delta v2 — Video Pipeline Pivot

**Applies to**: `specs/008-generation-pipeline/` | **Date**: 2026-06-16
**Triggered by**: SDD Ronda 6 (decisions #22-27); scope pivot from T2I cómic to
T2V video short.
**Read alongside**: the original `spec.md`, `research.md`, `tasks.md` in this
folder, and `specs/012-video-providers/` (the new module this delta depends on).

---

## 1. What changes, what stays, what is new

### Stays untouched
- T-001 (`winner_selector.py`) — winner pick logic unchanged.
- T-002 (`seed_derivation.py`) — same seed for clip prompts as for panel prompts.
- T-004 (scriptwriter prompts) — same narrative prompt files; only the
  output schema changes (see §3).
- T-006 (`r2_uploader.py`) — generic uploader; unchanged.
- T-008 (`scriptwriter.py` consumer) — unchanged; still calls `LLMProvider`.
- T-011 (DI registration) — unchanged; same entrypoint signature.
- T-012 / T-013 (rerun endpoint + CLI) — unchanged interface; coordinator
  internals change transparently.
- T-014 (live smoke) — extended, not replaced.
- T-015 (deploy + observe) — same acceptance bar.
- All FSM / DB schema / auth / voting / filter modules — untouched.

### Changes (existing tasks modified)
- **T-003** — `ScriptwriterResponse`: `panels` renamed to `clips`, count changes
  from `3-4` to `4-6`.
- **T-005** — `manifest_builder.py`: must produce both schema_version `"2.0"`
  (video path) and `"1.0"` (T2I fallback path).
- **T-007** — `tts_synthesizer.py`: per-clip audio segment (same fire-and-forget
  semantics, different call site).
- **T-009** — `panel_pipeline.py` becomes `clip_pipeline.py`: renders T2V clips
  via `VideoProviderRouter` instead of images via `ImageProviderRouter`.
- **T-010** — coordinator: new orchestration order (clips → stitch → T2I fallback
  on total T2V failure); deadline logic unchanged.

### New (added tasks)
- **T-016** — `stitch_pipeline.py`: ffmpeg concat clips + edge-tts audio mix →
  final chapter `.mp4`.
- **T-017** — ffmpeg system dependency: `Dockerfile` + `ffmpeg-python` Python dep.

---

## 2. New system dependency

**ffmpeg binary** required on the Fly machine.

`Dockerfile` change (in `apps/api/`):
```dockerfile
# After the base Python image
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
```

**Python wrapper**: `ffmpeg-python ~=0.2` (thin wrapper around ffmpeg subprocess;
avoids manual arg string construction and handles pipe errors cleanly).

```sh
uv add "ffmpeg-python~=0.2"
```

`mutagen` is already added by module 012 for duration validation in providers;
no duplicate needed here.

---

## 3. Changed and new Functional Requirements

### FR-003 delta — `ScriptwriterResponse` (replaces original FR-003 partially)

```python
class Clip(BaseModel):
    idx: int = Field(..., ge=1, le=8)
    narration: str = Field(..., min_length=10, max_length=500)
    visual_prompt: str = Field(..., min_length=20, max_length=400)  # English, validated
    mood: Literal["tense", "ominous", "contemplative", "hopeful",
                  "absurd", "melancholic", "euphoric", "dread", "tender"]
    tts_text: str = Field(..., min_length=10, max_length=500)

class ScriptwriterResponse(BaseModel):
    title: str = Field(..., min_length=5, max_length=80)
    synopsis: str = Field(..., min_length=20, max_length=400)
    clips: list[Clip] = Field(..., min_length=4, max_length=6)   # was panels[3..4]
    cliffhanger: str = Field(..., min_length=10, max_length=300)
    next_cliffhanger_seed: str = Field(..., min_length=10, max_length=300)
```

The field `panels` no longer exists in the primary path. The Pydantic model for
the T2I fallback path keeps the original `Panel` / `ScriptwriterResponse` under
`app/domain/scriptwriter_response_v1.py` (unchanged; only imported by the
fallback branch in the coordinator).

### FR-004 delta — Render clips (replaces original FR-004 entirely)

For each clip in `ScriptwriterResponse.clips`, in parallel (bounded by
`asyncio.Semaphore(CLIP_CONCURRENCY)`:

1. Compose the T2V prompt: `clip.visual_prompt + style.global_tags +
   style.negative_hint` (same composition rule as original T2I prompts).
2. Derive `seed = stable_hash(chapter_id, clip.idx)` (same helper as before).
3. Build `VideoRequest(prompt=…, seed=seed, duration_s=CLIP_DURATION_S,
   width=512, height=512, fps=24, aspect="9:16")`.
4. Call `VideoProviderRouter.render(req)` (from module 012).
5. Write bytes to `tmp_dir / f"clip_{clip.idx}.mp4"` (temp file, not in-memory).
6. Upload clip bytes to R2 via `compute_r2_clip_path` (module 012 helper).
7. Return `ClipResult`.

On `VideoProviderUnavailable` from the router (all T2V providers exhausted):
- If this is an **individual clip failure**: write the placeholder video bytes to
  `tmp_dir / f"clip_{clip.idx}.mp4"`; mark `ClipResult.ok=False`.
- If **all clips fail**: the coordinator catches `AllClipsFailedError` and
  triggers the **T2I fallback path** (§3 FR-018 below).

### FR-005 delta — TTS per-clip audio segment (replaces original FR-005)

For each clip (after its video renders), attempt TTS:

1. Call `synthesize(text=clip.tts_text, voice=TTS_VOICE)` → `bytes | None`.
2. If bytes: write to `tmp_dir / f"audio_{clip.idx}.mp3"`.
3. If None (TTS failed): log `tts_done {ok:false}`; this clip runs silent in
   the stitch. The chapter is NOT marked degraded for TTS failure (same
   fire-and-forget semantics as original FR-005 / research R-009).

### FR-017 NEW — Stitch pipeline (ffmpeg concat + audio mix)

After all clips render:

1. **Write concat list** to `tmp_dir / "clips_list.txt"`:
   ```
   file '/tmp/<uuid>/clip_1.mp4'
   file '/tmp/<uuid>/clip_2.mp4'
   ...
   ```
2. **Concat clips** (no re-encode if all clips share same codec/resolution):
   ```python
   ffmpeg.input(str(clips_list), format="concat", safe=0) \
         .output(str(tmp_dir / "video_only.mp4"), c="copy") \
         .run(quiet=True, overwrite_output=True)
   ```
3. **Concatenate audio segments**: stitch individual `audio_{idx}.mp3` files
   in order (any missing segment → insert silence of `CLIP_DURATION_S` length).
   ```python
   ffmpeg.concat(*audio_inputs, v=0, a=1) \
         .output(str(tmp_dir / "audio_track.mp3")) \
         .run(quiet=True, overwrite_output=True)
   ```
4. **Mix video + audio** into final chapter mp4:
   ```python
   video = ffmpeg.input(str(tmp_dir / "video_only.mp4"))
   audio = ffmpeg.input(str(tmp_dir / "audio_track.mp3"))
   ffmpeg.output(video, audio, str(tmp_dir / "chapter.mp4"),
                 vcodec="copy", acodec="aac",
                 shortest=None)  # cut at video end
         .run(quiet=True, overwrite_output=True)
   ```
5. Read `tmp_dir / "chapter.mp4"` bytes.
6. Upload to R2 at `seasons/{slug}/{chapter_public_id}/chapter-{sha256(bytes)[:8]}.mp4`.
7. Return `StitchResult(video_url, video_duration_s, video_bytes_len)`.
8. **Cleanup** `tmp_dir` contents after upload (temp files on Fly ephemeral
   storage; not needed post-upload).

**On ffmpeg failure**: raise `StitchError`; coordinator catches it, attempts
T2I fallback path.

### FR-018 NEW — T2V→T2I fallback at coordinator level

The coordinator implements a **two-level degradation strategy**:

```
Level 1 (T2V primary):
  render 4-6 clips → stitch → manifest v2.0 (video_mp4)
  └─ individual clip T2V failure → placeholder clip, chapter ready_degraded

Level 2 (T2I fallback — only if ALL clips fail OR stitch fails):
  render 3-4 panels via ImageProviderRouter (module 009)
  └─ manifest v1.0 (comic_panels), chapter ready OR ready_degraded
  └─ logged as: generation_t2i_fallback {reason, chapter_id}
```

The T2I fallback reuses the existing `panel_pipeline.py` (unchanged) and the
original `ScriptwriterResponse` Pydantic model. The scriptwriter is NOT re-called
in the fallback — the coordinator reuses the `ScriptwriterResponse` already in
memory, extracting `clip.narration` / `clip.visual_prompt` as panel equivalents
(clips 1-4 map to panels 1-4; clips 5-6 are dropped in fallback mode).

### FR-007 delta — Persist (replaces original FR-007 partially)

Same atomic transaction, but `manifest_json` now has two possible shapes:

- **Primary path** (T2V success): `schema_version="2.0"`, `manifest_kind=
  "video_mp4"` — shape documented in `specs/012-video-providers/data-model.md`.
- **Fallback path** (T2I): `schema_version="1.0"`, `manifest_kind="comic_panels"`
  — same shape as original module 008, with `manifest_kind` field added.

`manifest_builder.py` (T-005) handles both shapes via a dispatch on the pipeline
result type.

### FR-016 delta — New settings (appended to original FR-016)

```python
CLIP_CONCURRENCY: int = 4           # replaces PANEL_CONCURRENCY for T2V path
CLIP_DURATION_S: float = 5.0        # requested duration per T2V clip
PLACEHOLDER_VIDEO_URL: str          # static mp4 in R2 for failed clips
VIDEO_PIPELINE_ENABLED: bool = True # False → skip T2V, go straight to T2I
```

`PANEL_CONCURRENCY` is retained for the T2I fallback path (uses the same
default of 4).

---

## 4. Research delta

### R-014 — ffmpeg concat strategy: concat demuxer vs filtergraph

**Question**: use the concat demuxer (`-f concat`) or the concat filter
(`-filter_complex "concat=n=N:v=1:a=0"`)?

| Option | Pros | Cons |
|---|---|---|
| **Concat demuxer (chosen)** | No re-encode (stream copy); fast; simple list file | Clips must share codec, resolution, fps (they do — same provider, same params) |
| Filtergraph concat | Works with mixed codecs | Forces full re-encode; 2-5× slower; more memory |

**Decision**: concat demuxer. All clips come from the same provider call with
the same `VideoRequest` parameters — codec and resolution are uniform. The
`-c copy` flag avoids re-encoding and keeps stitch time < 5 s for 4-6 × 5 s
clips.

**Codec assumption**: HF LTX-Video and Pollinations video both return H.264/AAC
inside MP4. If a provider returns a different codec, `StitchError` surfaces and
the coordinator falls back to T2I. This is logged with `stitch_codec_mismatch`.

### R-015 — Temp files vs in-memory for clips

**Question**: hold clip bytes in memory and pipe to ffmpeg, or write to disk?

**Decision**: **temp files in `/tmp/`**.

**Rationale**:
- `ffmpeg-python` (and ffmpeg itself) works most reliably with named files.
  Pipe mode (`-pipe:0`) for the concat demuxer requires a named pipe per input,
  which is complex and OS-specific.
- Clip bytes: ~1-5 MB each × 6 clips = ~30 MB max on disk — well within Fly's
  ephemeral `/tmp` (free tier has ≥ 1 GB).
- Fly ephemeral storage is lost on restart, which is fine (temp files are
  cleaned up after upload regardless).

**Cleanup**: `shutil.rmtree(tmp_dir)` in a `finally` block in the coordinator,
after the stitched mp4 is uploaded (or after the T2I fallback completes).

### R-016 — ffmpeg on Fly free tier

**Question**: does the free Fly machine have enough CPU/memory for ffmpeg stitch?

**Decision**: **yes, within limits**.

- Fly free tier: 256 MB RAM, shared CPU.
- ffmpeg concat demuxer with `-c copy` is IO-bound (no transcoding): CPU < 5%,
  memory < 30 MB for 6 × 5 s clips.
- The `apt-get install ffmpeg` in the Dockerfile adds ~60 MB to the image; the
  free tier image size limit (1 GB) has headroom.
- Build time impact: ~20 s added to `docker build`. Acceptable.

**Verify in T-017**: run `RUN ffmpeg -version` in Dockerfile to fail the build
early if ffmpeg is unavailable rather than failing at runtime.

---

## 5. Changed contracts / interfaces

### `ClipResult` (new, replaces `PanelResult` in the T2V path)

```python
# app/domain/clip_pipeline.py
from dataclasses import dataclass

@dataclass
class ClipResult:
    idx: int
    clip_url: str           # R2 URL of the individual clip mp4
    clip_path: str          # local tmp path (for ffmpeg stitch input)
    tts_path: str | None    # local tmp path for audio segment (None if TTS failed)
    duration_s: float
    provider_used: str      # "hf" | "pollinations" | "placeholder"
    ok: bool                # False if placeholder video used
```

`PanelResult` (in `app/domain/panel_pipeline.py`) is NOT deleted — it stays for
the T2I fallback path.

### `StitchResult` (new)

```python
# app/domain/stitch_pipeline.py
from dataclasses import dataclass

@dataclass
class StitchResult:
    video_url: str          # R2 public URL of the final chapter mp4
    video_duration_s: float # actual total duration (from mutagen post-stitch)
    video_bytes_len: int    # total size in bytes (for logging)
```

### `AllClipsFailedError` (new exception)

```python
# app/domain/clip_pipeline.py
class AllClipsFailedError(Exception):
    """Raised by the coordinator when every ClipResult.ok is False."""
```

### Updated coordinator signature (T-010)

```python
# app/domain/generation_pipeline.py
async def generation_pipeline(chapter_id: int) -> None:
    """
    Same entrypoint as original. Orchestration order:

    1. pick_winner()
    2. scriptwriter.draft()              → ScriptwriterResponse (clips[])
    3. render_clips() [parallel]         → list[ClipResult]
    4.   if all failed → T2I fallback
    5. stitch_pipeline()                 → StitchResult
    6.   if stitch fails → T2I fallback
    7. manifest_builder.build_video()    → manifest_json v2.0
    8. persist (chapter INSERT + cycle UPDATE)
    9. cycle_executor.transition(PENDING_RELEASE)
    """
```

---

## 6. Task delta (changes to `tasks.md`)

### Modified tasks

**T-003** (`ScriptwriterResponse`) — Update `Panel` → `Clip`, `panels[3..4]`
→ `clips[4..6]`. Keep old `Panel` + `ScriptwriterResponse` classes in
`scriptwriter_response_v1.py` for the T2I fallback path. Tests: update all
existing tests + add test for `clips` length validation (4..6).

**T-005** (`manifest_builder.py`) — Add:
- `build_video(clips, stitch_result, winner_meta, gen_meta) -> dict` → v2.0.
- `build_comic(panels, winner_meta, gen_meta) -> dict` → v1.0 (renamed from
  original; logic unchanged).
- Tests: both shapes validate against expected `manifest_kind`, `schema_version`.

**T-007** (`tts_synthesizer.py`) — No API change. Call site changes: invoked
once per clip (in `clip_pipeline.py`), not once per panel. Tests unchanged.

**T-009** (`panel_pipeline.py` → `clip_pipeline.py`) — Replace file:
- Old: `render_panel(*, panel, …, image_router, …) -> PanelResult`
- New: `render_clip(*, clip, …, video_router, tmp_dir, …) -> ClipResult`
- Keep `panel_pipeline.py` unchanged (used in T2I fallback path).
- Tests: unit tests for `render_clip` (happy path, T2V failure → placeholder,
  TTS failure → ok=True with tts_path=None).

**T-010** (coordinator) — Significant rework:
- Import `VideoProviderRouter` + `chain_for_env` from `app.providers.video`.
- New orchestration: clips → stitch → T2I fallback on failure.
- `VIDEO_PIPELINE_ENABLED=False` bypasses T2V entirely and goes straight to T2I.
- New integration tests:
  - `test_generation_t2v_happy.py` — full T2V path, manifest v2.0.
  - `test_generation_t2v_all_clips_fail_t2i_fallback.py` — all T2V fails → T2I.
  - `test_generation_stitch_fail_t2i_fallback.py` — stitch fails → T2I.
  - `test_generation_video_disabled_t2i_direct.py` — `VIDEO_PIPELINE_ENABLED=False`.
  - Keep all existing tests from original T-010 (they now exercise the T2I path).

### New tasks

**T-016 — `stitch_pipeline.py`** → T-009 (clip_pipeline must be done first)

**Files**:
- `apps/api/app/domain/stitch_pipeline.py`
- `apps/api/tests/unit/test_stitch_pipeline.py`

**API**:
```python
async def stitch_clips(
    *,
    clips: list[ClipResult],
    tmp_dir: Path,
    uploader: R2Uploader,
    season_slug: str,
    chapter_public_id: UUID,
) -> StitchResult: ...
```

**Tests** (all with temp files; no real ffmpeg needed — mock `ffmpeg.run`):
- Happy path: N clips → one StitchResult with correct URL format.
- Missing audio segments (TTS failed) → silence inserted, stitch proceeds.
- All TTS missing → video-only stitch (no audio track), ok=True.
- ffmpeg subprocess error → `StitchError` raised.
- Cleanup: `tmp_dir` is empty after stitch (files deleted).

**T-017 — ffmpeg system dependency** → (no code dependency; can be done any time)

**Files**:
- `apps/api/Dockerfile` (add `apt-get install ffmpeg`)
- `apps/api/pyproject.toml` (add `ffmpeg-python~=0.2`)
- `apps/api/tests/unit/test_ffmpeg_available.py` (assert `shutil.which("ffmpeg")
  is not None`)
- `apps/api/scripts/upload_static_assets.py` (extend: upload `placeholder.mp4`
  alongside existing `placeholder.webp`)
- `assets/placeholder.mp4` (5-second black card with "…" text; generate once
  with ffmpeg locally and commit the binary)

**`placeholder.mp4` generation (one-shot, local)**:
```sh
ffmpeg -f lavfi -i color=c=black:s=512x512:d=5 \
       -vf "drawtext=text='…':fontcolor=white:fontsize=96:x=(w-text_w)/2:y=(h-text_h)/2" \
       -c:v libx264 -pix_fmt yuv420p \
       assets/placeholder.mp4
```

---

## 7. Updated log events (delta to FR-015)

New events (appended to the original list):

- `clip_render_started {clip_idx, seed, duration_s}`.
- `clip_render_done {clip_idx, provider, model, latency_ms, ok}`.
- `stitch_started {clip_count, clips_with_audio, clips_without_audio}`.
- `stitch_done {video_url, video_duration_s, video_bytes_len, latency_ms}`.
- `generation_t2i_fallback {reason, chapter_id}` — reason ∈
  `all_clips_failed | stitch_failed | video_pipeline_disabled`.
- `stitch_codec_mismatch {clip_idx, expected_codec, actual_codec}`.

The original events (`panel_render_started`, `panel_render_done`) remain; they
fire on the T2I fallback path.

---

## 8. Updated "Done when" criteria

Appended to the original module 008 done-when:

5. A T2V generation run on Fly produces a `manifest_kind="video_mp4"` chapter
   with a playable `.mp4` at the `video_url` (verified via `mutagen` parse).
6. A forced `VIDEO_PIPELINE_ENABLED=False` run produces a `manifest_kind=
   "comic_panels"` chapter (T2I path still works).
7. A simulated "all T2V providers exhausted" run (with `FakeVideoProvider`
   configured to raise `VideoProviderUnavailable`) falls back to T2I and
   produces a `comic_panels` chapter with `generation_t2i_fallback` in logs.

---

## 9. Estimate delta

| Phase | Original est. | Delta change | New est. |
|---|---|---|---|
| T-003 (ScriptwriterResponse) | included in Phase 0 (3d) | +0.5d (v1 kept alongside) | — |
| T-005 (manifest_builder) | included in Phase 0 (3d) | +0.5d (dual schema) | — |
| T-007 (tts_synthesizer) | Phase 1 (2d) | no change | — |
| T-009 (clip_pipeline, new) | Phase 2 (2.5d) | replacement, similar size | — |
| T-010 (coordinator) | Phase 3 (3d) | +1.5d (fallback logic + new tests) | — |
| T-016 (stitch_pipeline, NEW) | — | +2d | +2d |
| T-017 (ffmpeg dep, NEW) | — | +0.5d | +0.5d |
| **Delta total added** | | | **+5d** |

Original 008 estimate: ~20 working days (with buffer).
**New estimate: ~25 working days** (with buffer).
