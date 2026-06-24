# Delta v3 — I2V + 14s composition (Ronda 7 pivot)

**Applies to**: `specs/008-generation-pipeline/` | **Date**: 2026-06-24
**Triggered by**: SDD Ronda 7 (decisions #28-#34, ADR-0008).
**Read alongside**: original `spec.md`, `tasks.md`, the v2 `delta-video.md`
(Ronda 6) — this v3 sits **on top of** v2, not in place of it. Modules
referenced: `012-video-providers` delta (`ImageToVideoProvider`),
`013-characters-catalog` (`photo_r2_key`), `005-twists-submission` delta
(`character_id` on `twists`).

---

## 1. What changes, what stays, what is new

### Stays untouched (from v1 and v2)
- T-001 (`winner_selector.py`) — winner pick unchanged; the winner's
  `character_id` is now read from the joined `characters` table.
- T-002 (`seed_derivation.py`) — same seed scheme; reused for the
  single I2V clip.
- T-004 (`scriptwriter.py` consumer) — still calls `LLMProvider`. Only
  the **output schema** changes (see T-003 delta below).
- T-006 (`r2_uploader.py`) — unchanged.
- T-008 (`scriptwriter.py` Service) — unchanged.
- T-012 / T-013 (rerun endpoint + CLI) — unchanged interface;
  internals follow the new orchestration order (T-010 delta).
- T-014 (live smoke) — extended in this delta (T-018), not replaced.
- T-017 (Dockerfile ffmpeg) — done in v2; not re-touched.
- All FSM / DB schema (other than the new tables added by 005 + 012
  deltas) / auth / voting / filter modules — untouched.

### Changes (existing tasks modified — Ronda 7 layer)

- **T-003 delta** — `ScriptwriterResponse` reduces from `clips: list[Clip] (4..6)`
  to a **single** scene. See §3 FR-003 v3.
- **T-005 delta** — `manifest_builder.py` adds `schema_version "3.0"`
  for the I2V composition; `"2.0"` (T2V slideshow) survives as fallback
  for the case where I2V fails but T2V succeeds; `"1.0"` (T2I cómic)
  stays as last-resort. The builder is a discriminated-union router
  over (success_layer, version).
- **T-007 delta** — `tts_synthesizer.py`: still produces narration
  audio, but now **one segment** matching the 10 s Kling clip. The
  intro and outro carry no TTS (intro is silent ffmpeg overlay; outro
  has a pre-baked audio bed).
- **T-009 delta** — `clip_pipeline.py` (renamed in v2 from
  `panel_pipeline.py`) is rewritten:
  - Reads the winner's `twist.character_id` → joins
    `characters` → builds `image_url` from `photo_r2_key`.
  - Calls `ImageToVideoProviderRouter.render(ImageToVideoRequest(
        image_url, prompt=scene.visual_prompt, duration_s=10, aspect="9:16"
    ))`.
  - **One** clip per chapter. Concurrency knob `CLIP_CONCURRENCY` is
    removed (no parallel clips to coordinate).
  - On `ImageToVideoProviderUnavailable("budget_killswitch")` or any
    other I2V failure: surfaces to the coordinator (T-010) which
    invokes the T2V chain instead.
- **T-010 delta** — coordinator orchestration:
  - **Layer A (I2V)**: call `clip_pipeline.run_i2v(chapter)`. If success
    → proceed to T-016 stitch with the I2V mp4 + intro + outro.
  - **Layer B (T2V)**: on Layer A failure → call the v2 path
    (`clip_pipeline.run_t2v_clips(chapter)` — multiple clips + edge-tts
    + ffmpeg concat). manifest_version `2.0`.
  - **Layer C (T2I)**: on Layer B failure → call the v1 path
    (`panel_pipeline.run_t2i(chapter)`). manifest_version `1.0`.
  - The selection is logged: `chapter_render_layer {layer, reason}`.
- **T-016 delta** — `stitch_pipeline.py` is rewritten for Layer A:
  - Inputs: I2V mp4 bytes (10 s), winner's `display_name` (string),
    intro background URL (`GENERATION_INTRO_BG_URL`), outro mp4 URL
    (`GENERATION_OUTRO_URL`).
  - Pipeline (all via ffmpeg, see §4):
    1. **Intro 2 s**: download intro_bg.png; ffmpeg `drawtext` with the
       display_name; encode to `intro.mp4` (no audio).
    2. **Body 10 s**: the Kling mp4 bytes are written to a temp file
       (`body.mp4`).
    3. **Outro 2 s**: download outro.mp4 (pre-baked, has its own audio).
    4. **Concat**: ffmpeg `concat` demuxer on
       `intro.mp4 | body.mp4 | outro.mp4` → final `chapter.mp4`. Total
       duration is **exactly 14 s**.
    5. Upload to R2 at the existing chapter path
       (`compute_r2_chapter_path`).
  - For Layer B, the v2 stitch (clips + edge-tts) remains intact.

### New (added tasks)

- **T-018** — `intro_overlay.py` helper (pure ffmpeg call): renders
  `intro.mp4` (2 s) from `intro_bg.png` + display_name. Tested with a
  fixture name; output mp4 round-trips through `mutagen` with the
  expected duration.
- **T-019** — Static asset bootstrap: extend
  `app.scripts.upload_static_assets` (started in module 013 delta) to
  also upload `assets/intro_bg.png` and `assets/outro.mp4`. The two
  files are committed to the repo (small, < 200 KB combined). Outro is
  produced once with ffmpeg + the agency-style copy from PO.
- **T-020** — Live smoke for the full Layer A path (`@pytest.mark.live`):
  fakes Kling with a known-good mp4, runs the real stitch, asserts the
  final mp4 is 14 ± 0.2 s, uploads to R2 staging.

---

## 2. New dependencies

- None. `ffmpeg-python` and `mutagen` were already added in v2; reused
  here.
- New static assets (committed to the repo, uploaded to R2 by T-019):
  `assets/intro_bg.png` (PNG, 512×512 or 9:16 aspect, branded background),
  `assets/outro.mp4` (mp4, 2 s, pre-baked CTA).
- New env vars (in `settings.py`, loaded from Fly secrets):

| Var | Type | Default | Notes |
|---|---|---|---|
| `GENERATION_INTRO_BG_URL` | `str` | `${R2_PUBLIC_BASE_URL}/static/intro_bg.png` | Computed default if unset. |
| `GENERATION_OUTRO_URL` | `str` | `${R2_PUBLIC_BASE_URL}/static/outro.mp4` | Computed default if unset. |
| `GENERATION_INTRO_DURATION_S` | `float` | `2.0` | Composition constant. |
| `GENERATION_OUTRO_DURATION_S` | `float` | `2.0` | Composition constant. |
| `GENERATION_INTRO_FONT_SIZE` | `int` | `64` | ffmpeg `drawtext` size. |
| `GENERATION_INTRO_FONT_COLOR` | `str` | `white` | ffmpeg `drawtext` color. |

---

## 3. Changed and new Functional Requirements

### FR-003 v3 — `ScriptwriterResponse` (single scene)

```python
class Scene(BaseModel):
    narration: str = Field(..., min_length=10, max_length=500)
    visual_prompt: str = Field(..., min_length=20, max_length=400)   # English
    mood: Literal["tense", "ominous", "contemplative", "hopeful",
                  "absurd", "melancholic", "euphoric", "dread", "tender"]
    tts_text: str = Field(..., min_length=10, max_length=500)


class ScriptwriterResponseV3(BaseModel):
    title: str = Field(..., min_length=5, max_length=80)
    synopsis: str = Field(..., min_length=20, max_length=400)
    scene: Scene                                          # was clips[4..6]
    cliffhanger: str = Field(..., min_length=10, max_length=300)
    next_cliffhanger_seed: str = Field(..., min_length=10, max_length=300)
```

The v2 `ScriptwriterResponse` (with `clips[4..6]`) lives at
`app/domain/scriptwriter_response_v2.py` and is imported by the Layer B
branch in the coordinator. The v1 (panels[3..4]) at
`scriptwriter_response_v1.py` survives untouched for Layer C.

### FR-NEW-1 — Intro overlay

```python
async def render_intro(
    background_url: str,
    display_name: str,
    out_path: Path,
) -> None:
    """Render a 2 s intro mp4 from a static background + dynamic name."""
    # ffmpeg -loop 1 -t 2 -i bg.png \
    #   -vf "drawtext=fontfile=…:text='$NAME':fontcolor=white:fontsize=64:
    #        x=(w-text_w)/2:y=(h-text_h)/2" \
    #   -c:v libx264 -pix_fmt yuv420p -shortest out.mp4
```

The fontfile path is taken from `settings.intro_font_path`
(`/app/assets/fonts/NotoSans-Bold.ttf`, vendored). `display_name` is
shell-escaped to prevent injection (covered by integration test
TIH-001).

### FR-NEW-2 — Final concat

```python
async def stitch_layer_a(
    intro_mp4: Path,
    body_mp4: Path,        # Kling I2V output
    outro_mp4: Path,
    out_path: Path,
) -> None:
    """Concatenate 2s + 10s + 2s = 14s using ffmpeg concat demuxer."""
    # Write a concat list file:
    #   file 'intro.mp4'
    #   file 'body.mp4'
    #   file 'outro.mp4'
    # ffmpeg -f concat -safe 0 -i list.txt -c copy out.mp4
```

The three inputs **must** share codec (libx264) and pix_fmt (yuv420p);
the body (Kling) and the outro (pre-baked) are constrained at R-NEW-1
of this delta and at the pre-bake step respectively. The intro
output is encoded with the same params.

Acceptance: final `chapter.mp4` duration ∈ [13.8 s, 14.2 s] (mutagen).

### FR-NEW-3 — Layer selection logging

```
chapter_render_layer {layer: "A" | "B" | "C", reason: "i2v_ok" | "budget_killswitch" | "i2v_failed" | "t2v_failed", chapter_id: …}
```

One log per chapter. Surfaces in the existing dashboard.

---

## 4. ffmpeg invocations — reference

### Intro

```sh
ffmpeg -y -loop 1 -t 2 -i intro_bg.png \
       -vf "drawtext=fontfile=NotoSans-Bold.ttf:text='${NAME}':fontcolor=white:fontsize=64:x=(w-text_w)/2:y=(h-text_h)/2" \
       -c:v libx264 -pix_fmt yuv420p -r 24 -shortest intro.mp4
```

### Body (Kling output)

Written verbatim from the Kling response. Validated codec (libx264) and
pix_fmt (yuv420p) by `mutagen.mp4`; if mismatched the body is
re-encoded transparently via:

```sh
ffmpeg -y -i body_raw.mp4 -c:v libx264 -pix_fmt yuv420p -r 24 -c:a aac body.mp4
```

(Re-encode adds ~5 s; tolerable inside the GENERACION window.)

### Concat

```sh
ffmpeg -y -f concat -safe 0 -i list.txt -c copy chapter.mp4
```

`list.txt`:

```
file 'intro.mp4'
file 'body.mp4'
file 'outro.mp4'
```

---

## 5. Tests delta

- **Unit**:
  - `render_intro` produces a 2 s mp4 with correct duration (mutagen).
  - `render_intro` shell-escapes single quotes and `\` in display_name
    (TIH-001, security regression).
  - `stitch_layer_a` returns a 14 s mp4 (mutagen).
  - Re-encode path triggers when body codec is unexpected.
- **Integration** (coordinator):
  - Layer A path succeeds end-to-end with `FakeImageToVideoProvider`
    returning the `MINIMAL_MP4` extended to 10 s.
  - On `ImageToVideoProviderUnavailable("budget_killswitch")` the
    coordinator silently switches to Layer B (the v2 path). Manifest
    version reflects `2.0`.
  - On total T2V failure (all providers exhausted), Layer C kicks in
    with v1 panels. Manifest version `1.0`.
  - The structured log `chapter_render_layer` fires exactly once per
    chapter, with the correct `layer` and `reason`.
- **Live**:
  - T-020 (gated by `KLING_API_KEY` env) runs the real chain once a
    night against staging R2.

---

## 6. Acceptance for "delta done"

- [ ] `clip_pipeline.run_i2v` exists, returns the Kling mp4 bytes.
- [ ] `stitch_pipeline.stitch_layer_a` produces a 14 s mp4 with the
      three concatenated segments.
- [ ] Intro overlay renders dynamic display_name; shell-escape test passes.
- [ ] Coordinator selects layer correctly per the 3-tier degradation.
- [ ] Manifest schema versions: `3.0` (I2V), `2.0` (T2V slideshow),
      `1.0` (T2I) coexist; PWA renders all three via
      `manifest_kind` discriminator.
- [ ] `assets/intro_bg.png` and `assets/outro.mp4` committed to the
      repo and uploaded to R2 by the extended script.
- [ ] All gates from the original 008 + v2 still pass; no new
      carve-outs introduced by this delta.
