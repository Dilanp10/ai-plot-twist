# Data Model: Director's Filter

**Branch**: `006-directors-filter` | **Date**: 2026-06-07

**No new tables.** This module reads + updates the `twists` table created by module
005. No migrations ship with this PR.

This file documents:
1. The UPDATE pattern the filter applies.
2. The prompt files contract (loaded at startup).
3. The LLM response schema (mirrored as JSON Schema in `contracts/`).

---

## UPDATE pattern

Per batch, the filter performs N + 1 writes inside a single transaction:

- N `UPDATE twists` statements (one per twist in the batch).
- 0 cycle-state writes during the batch loop. After ALL batches complete, ONE
  call to `cycle_executor.transition(to='VOTACION', ...)` which writes the cycle
  + `state_transitions` row.

```sql
BEGIN;

-- Per twist in the batch (parameterized):
UPDATE twists
   SET status           = :decision,        -- 'approved' | 'rejected_*'
       director_reason  = :reason,           -- ≤ 80 chars
       reviewed_at      = now()
 WHERE id = :twist_id
   AND status = 'pending_review';            -- guard: idempotent re-runs only touch pending
-- (Replay endpoint uses a relaxed guard; see below.)

COMMIT;
```

**Concurrency**: the filter runs as a `BackgroundTask` spawned from the 18:00
transition. The FSM's advisory lock (module 003) prevents a second FILTERING
transition; the cycle state guarantees no concurrent filters for the same chapter.
No twist-level locks needed.

**Replay endpoint** (FR-014) uses a relaxed UPDATE without the `status =
'pending_review'` guard, so it can re-classify already-classified twists. It still
excludes `deleted_by_user`:

```sql
UPDATE twists
   SET status           = :decision,
       director_reason  = :reason,
       reviewed_at      = now()
 WHERE id = :twist_id
   AND status != 'deleted_by_user';
```

---

## Prompt files contract

Two files live under `apps/api/app/prompts/`. The filter loads them once at module
import time and caches.

### `director_v1.system.txt`

Plain text. Content per SDD §4.2.2. Hash recorded as a constant in
`director_prompts.py`:

```python
DIRECTOR_V1_SYSTEM_SHA256 = "abcd…64hex"   # CI test asserts this matches the file
```

### `director_v1.user.j2`

Jinja2 template. Variables passed at render:

| Variable | Type | Source |
|---|---|---|
| `season` | `Season` ORM/DTO with `.bible_json` (full, NOT redacted) | `SeasonsRepo.get_active()` |
| `last_chapters` | `list[Chapter]` (last 3, ordered by `day_index` ASC) | `ChaptersRepo.list_by_season(limit=3, order_desc=True)` reversed |
| `current` | `Chapter` (the chapter being filtered) | from invocation arg |
| `batch` | `list[TwistForFilter]` where each has `.public_id, .content` | `TwistsRepo.list_pending_for_chapter(chapter_id)` chunked |

The template output is a single user-prompt string sent to the LLM.

**Why the full (un-redacted) bible**: the LLM is a trusted internal consumer — it
needs setting/tone/rules to judge coherence. It is NOT the public bible (module
004 owns redaction for users).

### Hash audit

`tests/unit/test_director_prompts.py::test_prompt_hashes_match` reads the prompt
files, computes sha256, and compares to the constants. Any prompt edit forces a
constant bump in the same PR — surfaces drift.

---

## LLM response schema

The Pydantic models passed as `response_schema` to Gemini:

```python
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, Field

class DirectorVerdict(BaseModel):
    twist_id: UUID
    decision: Literal[
        "approved",
        "rejected_offensive",
        "rejected_incoherent",
        "rejected_spam",
    ]
    reason: str = Field(..., max_length=80)

class DirectorBatchResponse(BaseModel):
    verdicts: list[DirectorVerdict]
```

This is mirrored as `contracts/director-response.schema.json` (JSON Schema 2020-12)
for documentation and for any future external consumer.

**Truncation**: if Gemini returns a `reason` > 80 chars despite the schema
constraint (Gemini's structured-output mode generally enforces, but is not 100 %),
the filter truncates server-side to 80 chars (last char becomes `…`) and logs
`director_reason_truncated`.

---

## Reads (no migrations needed)

The filter reads from existing tables:

| Query | Table | Index used |
|---|---|---|
| `SELECT * FROM twists WHERE chapter_id=? AND status='pending_review'` | `twists` | `idx_twists_chapter_status` (module 005) |
| `SELECT * FROM chapters WHERE id IN (...)` | `chapters` | PK |
| `SELECT * FROM seasons WHERE id=?` | `seasons` | PK |

All indexes present; no DDL changes.

---

## What this module does NOT touch

- `cycles.state` directly (delegated to `cycle_executor.transition`).
- `state_transitions` (executor writes those).
- `chapters.manifest_json` (read-only for prompt context).
- `users` (read-only at most; doesn't expose `user_id` to the LLM).
