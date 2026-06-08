# Data Model: Chapter Content Read API

**Branch**: `004-chapters-content` | **Date**: 2026-06-07

**No new tables.** This module is a read-only consumer of structures created by
module 003 (`seasons`, `chapters`, `cycles`, `system_flags`).

This file documents the **read queries** and confirms that the **indexes** required
already exist on the tables shipped by 003. If any index is missing, this module
ships a migration to add it; otherwise no DDL.

---

## Required read queries

### Q-1 — Active cycle joined with chapter + season (heart of `/chapters/today`)

```sql
SELECT
  c.id               AS cycle_id,
  c.state            AS cycle_state,
  c.state_entered_at,
  c.cycle_date,
  ch.public_id       AS chapter_public_id,
  ch.day_index,
  ch.title           AS chapter_title,
  ch.synopsis        AS chapter_synopsis,
  ch.manifest_json,
  ch.released_at,
  ch.status          AS chapter_status,
  s.slug             AS season_slug,
  s.title            AS season_title
FROM cycles c
JOIN chapters ch ON ch.id = c.chapter_id
JOIN seasons  s  ON s.id  = c.season_id
WHERE s.is_active = TRUE
LIMIT 1;
```

**Index path**:

| Step | Table | Index used | Verified by |
|---|---|---|---|
| Active season filter | `seasons` | `uniq_one_active_season` (partial UNIQUE) | `EXPLAIN ANALYZE` in test |
| Cycle FK | `cycles` | implicit PK | |
| Chapter FK | `chapters` | implicit PK | |

**Verdict**: no new indexes required.

**Expected plan cost**: ~3 page reads, ≤ 1 ms server-side.

### Q-2 — Chapter by `public_id` (heart of `/chapters/{id}`)

```sql
SELECT
  ch.public_id,
  ch.day_index,
  ch.title,
  ch.synopsis,
  ch.manifest_json,
  ch.released_at,
  ch.status,
  s.slug AS season_slug,
  s.title AS season_title
FROM chapters ch
JOIN seasons s ON s.id = ch.season_id
WHERE ch.public_id = :public_id
  AND ch.status IN ('live', 'archived')
LIMIT 1;
```

**Index path**:

| Step | Table | Index used |
|---|---|---|
| `public_id` lookup | `chapters` | implicit UNIQUE on `public_id` |
| Season FK | `seasons` | implicit PK |

**Verdict**: no new indexes required.

### Q-3 — Season by slug + chapter count + current day (heart of `/seasons/{slug}`)

```sql
SELECT
  s.slug,
  s.title,
  s.bible_json,
  s.started_on,
  s.ended_on,
  COUNT(ch.id) FILTER (WHERE ch.status IN ('live', 'archived')) AS chapter_count,
  (SELECT MAX(ch2.day_index)
     FROM chapters ch2
     WHERE ch2.season_id = s.id
       AND ch2.status = 'live') AS current_day_index
FROM seasons s
LEFT JOIN chapters ch ON ch.season_id = s.id
WHERE s.slug = :slug
GROUP BY s.id;
```

**Index path**:

| Step | Table | Index used |
|---|---|---|
| Slug lookup | `seasons` | implicit UNIQUE on `slug` |
| Chapter aggregation | `chapters` | sequential within season (small, OK) |
| `current_day_index` subquery | `chapters` | `idx_chapters_status_release` covers `status='live'` |

**Verdict**: no new indexes required. The aggregation scan is bounded by the
season's chapter count (≤ ~30 in MVP) and runs once per request — well within
budget.

### Q-4 — Kill-switch read (every request)

```sql
SELECT flag_value FROM system_flags WHERE flag_key = 'kill_switch';
```

**Cache**: 30 s in-process LRU (inherited from module 003 R-005). On cache miss,
1 PK read. ≤ 0.5 ms.

---

## Response computation

The path from query result to JSON response runs entirely in Python:

1. Q-1 returns one row.
2. `compute_windows(cycle, now_utc)` produces the four window timestamps.
3. `manifest_json` is parsed into a typed Pydantic model:
   - `panels: list[Panel]` where `Panel = {idx, image_url, image_blurhash?, tts_url?,
     narration, mood}`.
   - `cliffhanger: str`.
   - `next_cliffhanger_seed: str` (private; not exposed in response).
4. `etag = derive_etag(chapter, cycle)` (see research R-005).
5. Cache headers attached (see spec FR-007).
6. Response serialized.

Total Python work per request: < 1 ms in synthetic profiling.

---

## Schema invariants this module relies on

If module 003 ever changes any of these, module 004's tests will catch it; document
the contract here so future PRs don't quietly break us:

- `seasons.is_active` partial UNIQUE: at most ONE active season at a time.
- `cycles.chapter_id` is NOT NULL and always references a `chapters` row whose
  `season_id` matches `cycles.season_id` (semantic invariant — there is no DB
  check, but module 003's bootstrap and executor uphold it).
- `chapters.status` lifecycle: `draft → generating → ready/ready_degraded →
  live → archived`. Q-2 includes both `live` and `archived` so re-reading
  yesterday's chapter works.
- `chapters.manifest_json` is shaped as `{panels: [...], cliffhanger: str,
  next_cliffhanger_seed: str, winner_metadata?: {...}}`. The keys this module
  reads: `panels`, `cliffhanger`. Tolerant of additional keys.
- `system_flags.kill_switch.flag_value` shape: `{on: bool, reason: string|null,
  set_at?: string}`.

---

## What this module does NOT touch

- No `INSERT`, `UPDATE`, `DELETE` against any table.
- No background tasks.
- No advisory locks.
- No new migrations (subject to the verification noted in Q-3 holding in practice).
