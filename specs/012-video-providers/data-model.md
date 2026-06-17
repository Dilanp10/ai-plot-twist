# Data Model: VideoProvider Abstraction

**Branch**: `012-video-providers` | **Date**: 2026-06-16

**No new tables, no migrations.** This module is pure infrastructure, parallel
to module 009. What this file documents instead:

1. The **R2 path contract** for individual clips (analogous to 009's panel path).
2. The **updated `chapters.manifest_json` shape** (schema_version 2.0) that
   module 008's delta will write when the video pipeline succeeds.
3. The **backward-compat strategy** for the T2I degradation path
   (schema_version 1.0 chapters co-existing with 2.0 chapters).

---

## R2 path contract — individual clips

```
seasons/{season_slug}/{chapter_public_id}/clips/{clip_idx}-{content_hash}.mp4
```

| Segment | Source | Constraints |
|---|---|---|
| `seasons/` | Constant prefix | Same as module 009 |
| `{season_slug}` | `seasons.slug` from DB | Lowercase + hyphens; max 40 chars |
| `{chapter_public_id}` | `chapters.public_id` (UUID v4) | Unguessable; prevents future-chapter enumeration |
| `clips/` | Constant sub-prefix | Separates individual clips from the stitched final below |
| `{clip_idx}` | Integer 0..N-1 | Zero-indexed; human-readable in ops logs |
| `{content_hash}` | `sha256(video_result.bytes_)[:8]` (8 hex chars) | Deterministic; allows idempotent R2 PUT |
| `.mp4` | Fixed extension | Only MIME type accepted from T2V providers in MVP |

**Example**:
```
seasons/s01-el-tunel/9f3a3b5f-7e2c-4d4f-a1b2-c3d4e5f60718/clips/2-a1b2c3d4.mp4
```

**Invariants** (same as module 009's path contract):
1. **Deterministic**: same input → same path. R2 PUT is idempotent.
2. **Content-addressed**: different prompt/seed → different bytes → different hash.
   CDN caches (Cloudflare, SW) invalidate automatically on URL change.
3. **Enumeration resistance**: `chapter_public_id` is a fresh UUID; `content_hash`
   is unguessable until generation completes.

---

## R2 path contract — stitched final video

The stitched chapter `.mp4` (individual clips + edge-tts audio merged by module
008) is uploaded to a sibling path **without** `clips/`:

```
seasons/{season_slug}/{chapter_public_id}/chapter-{content_hash}.mp4
```

This path is **not** computed by module 012. It is module 008's responsibility
(just as uploading images to R2 is 008's job, not 009's). It is documented
here for cross-module visibility.

---

## Helper API — `compute_r2_clip_path`

```python
# app/providers/video/paths.py
import hashlib
from app.providers.video.base import VideoResult

def compute_r2_clip_path(
    season_slug: str,
    chapter_public_id: str,   # UUID as str (lowercase, with hyphens)
    clip_idx: int,             # 0-indexed
    video_result: VideoResult,
) -> str:
    content_hash = hashlib.sha256(video_result.bytes_).hexdigest()[:8]
    return (
        f"seasons/{season_slug}/{chapter_public_id}/"
        f"clips/{clip_idx}-{content_hash}.mp4"
    )
```

**Test coverage** (in `test_video_paths.py`):
- Same `(slug, uuid, idx, result)` → same path (idempotency).
- Different bytes → different hash → different path.
- Path matches regex
  `^seasons/[a-z0-9-]+/[0-9a-f-]{36}/clips/\d+-[0-9a-f]{8}\.mp4$`.

---

## `chapters.manifest_json` — schema_version 2.0 (video)

Module 008's delta writes this shape when the video pipeline succeeds:

```jsonc
{
  "schema_version": "2.0",
  "manifest_kind": "video_mp4",

  // ── Final stitched output ────────────────────────────────────────────────
  "video_url": "https://assets.aiplottwist.example/seasons/s01-el-tunel/9f3a…/chapter-ab12cd34.mp4",
  "video_duration_s": 32.5,

  // ── Individual clips (pre-stitch, kept for debugging / rerun) ────────────
  "clips": [
    {
      "idx": 0,
      "clip_url": "https://assets.aiplottwist.example/seasons/s01-el-tunel/9f3a…/clips/0-a1b2c3d4.mp4",
      "duration_s": 5.0,
      "narration": "El espejo crujió como hielo viejo…",
      "mood": "tense",
      "provider": "hf",
      "model": "ltx-video"
    }
    // 3 to 5 more clips
  ],

  // ── Narrative continuity (same fields as v1.0) ──────────────────────────
  "cliffhanger": "Una voz —la suya— le respondió desde el otro lado.",
  "next_cliffhanger_seed": "El espejo está roto pero la voz sigue.",

  // ── Attribution (same shape as v1.0) ────────────────────────────────────
  "winner_metadata": {
    "winner_twist_id": "b1c2d3e4-…",
    "winner_author_display_name": "Lucía",
    "vote_count": 12,
    "tiebreak": false,
    "runner_up_twist_id": null
  },

  // ── Observability ────────────────────────────────────────────────────────
  "generation_metadata": {
    "manifest_kind": "video_mp4",
    "scriptwriter_model": "gemini-2.0-flash",
    "scriptwriter_provider": "gemini",
    "clip_provider_breakdown": {"hf": 4, "pollinations": 1},
    "tts_provider": "edge-tts",
    "ffmpeg_stitch": true,
    "started_at": "2026-06-16T02:00:00Z",
    "finished_at": "2026-06-16T02:38:44Z",
    "duration_ms": 2324000,
    "degraded": false,
    "degraded_reasons": []
  }
}
```

**Field notes**:

| Field | Notes |
|---|---|
| `schema_version` | Bumped to `"2.0"` for video. Module 004 and module 010 must branch on this. |
| `manifest_kind` | `"video_mp4"` here. PWA uses this to decide render path (player vs comic viewer). |
| `video_url` | Public R2 URL of the stitched `.mp4`. Module 004 exposes this. |
| `video_duration_s` | Actual total duration after stitch (may differ slightly from sum of clips). |
| `clips[]` | Retained for ops debugging and future rerun granularity. NOT exposed to end users by module 004. |
| `clips[].narration` | Text fed to edge-tts for that clip's audio segment. Exposed by module 004 as caption / accessibility transcript. |
| `generation_metadata.degraded` | `true` if any clip fell back to a secondary T2V provider. NOT `true` if the T2I fallback was triggered — in that case `manifest_kind` itself is `"comic_panels"`. |

---

## `chapters.manifest_json` — schema_version 1.0 / backward compat (T2I degradation path)

When the entire T2V chain fails, module 008 falls back to module 009 (T2I) and
writes a **schema_version 1.0** manifest — same shape as today. The only
addition is the `manifest_kind` field:

```jsonc
{
  "schema_version": "1.0",
  "manifest_kind": "comic_panels",   // NEW field — was absent in original 1.0

  "panels": [
    {
      "idx": 1,
      "image_url": "…",
      "image_blurhash": "…",
      "tts_url": "…",
      "narration": "…",
      "mood": "tense"
    }
  ],
  "cliffhanger": "…",
  "next_cliffhanger_seed": "…",
  "winner_metadata": { … },
  "generation_metadata": {
    "manifest_kind": "comic_panels",
    "degraded": true,
    "degraded_reasons": ["t2v_chain_exhausted"]
    // … rest same as today
  }
}
```

**Backward-compat rule**: if `manifest_kind` is absent, treat as
`"comic_panels"`. This covers the Capítulo 0 manifest (written manually by the
PO before this module ships) and any chapter generated before the delta of
module 008 lands. No migration required.

---

## Schema evolution summary

| schema_version | manifest_kind | Written by | When |
|---|---|---|---|
| `"1.0"` (no `manifest_kind`) | — (treat as `comic_panels`) | module 008 (today) | Before video pivot lands |
| `"1.0"` + `manifest_kind: "comic_panels"` | `comic_panels` | module 008 delta | T2V chain exhausted → T2I fallback |
| `"2.0"` + `manifest_kind: "video_mp4"` | `video_mp4` | module 008 delta | T2V pipeline succeeds |

**PWA (module 010) branching logic** (pseudocode):
```
kind = manifest.manifest_kind ?? "comic_panels"
if kind == "video_mp4":
    render <VideoPlayer src={manifest.video_url} />
else:
    render <ComicViewer panels={manifest.panels} />
```

---

## What this module does NOT define

- The **R2 upload** of the stitched `chapter.mp4`. Module 008 delta's job.
- The **ffmpeg stitch command** or edge-tts call. Module 008 delta's job.
- The **public URL prefix** (`https://assets.aiplottwist.example/…`). R2 /
  Cloudflare config, owned by module 001's `infra/`.
- The **caching headers** on the final mp4 asset. R2 bucket policy.
- The **PWA video player component**. Module 010's job.

---

## Future schema implications

If the chapter asset inventory grows, a `chapter_assets` table could index every
uploaded R2 object (clips + stitched mp4) with `content_hash`, `provider`,
`asset_kind` (`clip` / `chapter_video` / `panel_image`), `generated_at`. The
path scheme remains stable. **Not in MVP.**
