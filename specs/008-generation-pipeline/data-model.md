# Data Model: Generation Pipeline

**Branch**: `008-generation-pipeline` | **Date**: 2026-06-07

**No new tables, no migrations.** This module reads from `twists`, `votes`,
`seasons`, and the source `chapters` row; it INSERTs one new `chapters` row per
day and UPDATEs `cycles.next_chapter_id`. Schema mirrors module 003 §3.1
exactly.

This file documents:
1. The **winner-selection SQL** (verbatim per SDD §4.3 + tiebreak transparency).
2. The **`chapters.manifest_json` shape** versioned for forward compat.
3. The **persistence transaction**.

---

## Winner-selection SQL

```sql
WITH ranked AS (
  SELECT
    t.id,
    t.public_id,
    t.user_id,
    t.content,
    t.submitted_at,
    COUNT(v.id) AS vote_count,
    ROW_NUMBER() OVER (
      ORDER BY COUNT(v.id) DESC, t.submitted_at ASC, t.id ASC
    ) AS rn
  FROM twists t
  LEFT JOIN votes v ON v.twist_id = t.id
  WHERE t.chapter_id = :chapter_id
    AND t.status = 'approved'
  GROUP BY t.id
)
SELECT
  r.id, r.public_id, r.user_id, r.content,
  r.submitted_at, r.vote_count, r.rn,
  u.display_name
FROM ranked r
JOIN users u ON u.id = r.user_id
WHERE r.rn <= 2
ORDER BY r.rn;
```

- Returns 0, 1, or 2 rows.
- Row 1 (`rn=1`) is the winner.
- Row 2 (`rn=2`) is the runner-up (used for the `winner_metadata.runner_up_twist_id`
  field). Only persisted when `tiebreak=true` (i.e., row 1 and row 2 share the
  same `vote_count`).
- 0 rows → auto-continue mode (winner_twist=None).

**Index path**: `idx_twists_chapter_status` (module 005) + `idx_votes_twist`
(module 007). Verified by `EXPLAIN ANALYZE` in test.

---

## `chapters.manifest_json` shape (schema_version 1.0)

```jsonc
{
  "schema_version": "1.0",
  "panels": [
    {
      "idx": 1,
      "image_url": "https://assets.aiplottwist.example/seasons/s01-el-tunel/9f3a…/1-a1b2c3d4.webp",
      "image_blurhash": "LKO2?V%2Tw=w]~RBVZRi};RPxuwH",
      "tts_url": "https://assets.aiplottwist.example/seasons/s01-el-tunel/9f3a…/1-tts-eeff0011.mp3",
      "narration": "El espejo crujió como hielo viejo…",
      "mood": "tense"
    }
    // 3 to 4 panels total
  ],
  "cliffhanger": "Una voz —la suya— le respondió desde el otro lado.",
  "next_cliffhanger_seed": "El espejo está roto pero la voz sigue.",
  "winner_metadata": {
    "winner_twist_id": "b1c2d3e4-…",     // null in auto-continue mode
    "winner_author_display_name": "Lucía", // null in auto-continue mode
    "vote_count": 12,
    "tiebreak": false,
    "runner_up_twist_id": null
  },
  "generation_metadata": {
    "scriptwriter_model": "gemini-2.0-flash",
    "scriptwriter_provider": "gemini",
    "panel_provider_breakdown": {"pollinations": 3, "hf": 0, "placeholder": 1},
    "tts_provider": "edge-tts",
    "started_at": "2026-06-08T02:00:00Z",
    "finished_at": "2026-06-08T02:42:11Z",
    "duration_ms": 2531000,
    "degraded": true,
    "degraded_reasons": ["panel_2_render_failed"]
  }
}
```

**Mutable / immutable**:

- `panels`, `cliffhanger`, `next_cliffhanger_seed`, `winner_metadata`,
  `generation_metadata` — written ONCE at chapter creation. Module 004 reads
  them. Module 008 only mutates them on rerun (overwrites in place + bumps
  `released_at`).

**Module 004 contract**: reads `panels[*].image_url`, `panels[*].image_blurhash`,
`panels[*].tts_url`, `panels[*].narration`, `panels[*].mood`, `cliffhanger`. Does
NOT read `winner_metadata` or `generation_metadata`. The PWA may surface
`winner_metadata.winner_author_display_name` ("Plot twist por @Lucía") via a
future addition to module 004's response; not in this module's scope.

`bible_redaction` (module 004) explicitly excludes `winner_metadata` and
`generation_metadata` if a future change moves them into the bible — they stay
in the chapter manifest.

---

## Persistence transaction (SQL outline)

The body of `generation_pipeline.finalize(chapter_id, manifest, status)`:

```sql
BEGIN;

-- 1. Insert the next chapter row
INSERT INTO chapters
  (season_id, day_index, title, synopsis, manifest_json, status, released_at, created_at)
VALUES
  (:season_id, :next_day_index, :title, :synopsis, :manifest, :status, NULL, now())
RETURNING id, public_id;
-- :status is 'ready' or 'ready_degraded'

-- 2. Update cycle to point at it
UPDATE cycles
   SET next_chapter_id = :new_chapter_id
 WHERE id = :cycle_id;

COMMIT;

-- 3. (Outside transaction) Trigger cycle FSM transition to PENDING_RELEASE.
```

**`next_day_index`**: computed as `(SELECT MAX(day_index) FROM chapters WHERE
season_id = :season_id) + 1`. Pre-computed once before the transaction.

**Rerun-generation transaction**:

```sql
BEGIN;

UPDATE chapters
   SET title         = :new_title,
       synopsis      = :new_synopsis,
       manifest_json = :new_manifest,
       status        = :new_status,
       released_at   = now()       -- invalidates module 004's ETag
 WHERE public_id = :target_public_id;

COMMIT;
```

No cycle mutation — rerun only fixes content.

---

## Reads relied on

| Query | Table | Index |
|---|---|---|
| `pick_winner` CTE | `twists`, `votes`, `users` | `idx_twists_chapter_status`, `idx_votes_twist`, users PK |
| `chapter` lookup (current live for scriptwriter context) | `chapters` | PK |
| `season` lookup for bible | `seasons` | `uniq_one_active_season` |
| Last 3 chapters for prompt context | `chapters` | `idx_chapters_status_release` (orders by `released_at DESC`) |

All indexes from prior modules; no DDL changes in this module.

---

## What this module does NOT touch

- `twists.status` (read-only; the filter owns those mutations).
- `votes.*` (read-only).
- `cycles.state` directly (delegated to `cycle_executor.transition`).
- `state_transitions` (executor writes those).
- The currently `live` chapter's row (it's read for context but not mutated).
