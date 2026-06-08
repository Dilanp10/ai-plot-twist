# Task Breakdown: Chapter Content Read API

**Branch**: `004-chapters-content` | **Date**: 2026-06-07

PR-sized chunks.

---

## Phase 0 — Pure domain (3 PRs, parallel)

### T-001 — `bible_redaction.py` [P]
**Files**:
- `apps/api/app/domain/bible_redaction.py`
- `apps/api/tests/unit/test_bible_redaction.py`

**API**:
```python
PUBLIC_BIBLE_KEYS: frozenset[str] = frozenset({"setting","tone","characters","rules"})
def redact(bible: dict) -> dict: ...
```

**Tests**: subset property, unknown key excluded, empty dict input, deeply nested
allowed values preserved verbatim.

### T-002 — `windows.py` [P]
**Files**:
- `apps/api/app/domain/windows.py`
- `apps/api/tests/unit/test_windows.py`

**API**:
```python
@dataclass(frozen=True)
class Windows:
    submit_until: datetime
    vote_from: datetime
    vote_until: datetime
    next_release: datetime

def compute_windows(cycle_state: str, state_entered_at: datetime,
                    cycle_date: date, now_utc: datetime,
                    cycle_times: CycleTimes) -> Windows: ...
```

**Tests**: one per FSM state. Verify ART-to-UTC conversion. Verify that
`next_release` advances by 1 day after the current state passes ESTRENO.

### T-003 — `etag.py` [P]
**Files**:
- `apps/api/app/domain/etag.py`
- `apps/api/tests/unit/test_etag.py`

**API**:
```python
def derive_etag(chapter_public_id: UUID, cycle_state: str,
                released_at: datetime) -> str: ...   # 16 hex chars, no quotes
```

---

## Phase 1 — Infra + service (2 PRs)

### T-004 — `ContentRepo` joined read → 003-merged
**Files**:
- `apps/api/app/infra/content_repo.py`
- `apps/api/tests/integration/test_content_repo.py`

**Methods**: `get_today_payload() -> TodayPayload | None`,
`get_chapter_by_public_id(uuid) -> ChapterPayload | None`,
`get_season_by_slug(str) -> SeasonPayload | None`. Raw SQL queries Q-1, Q-2, Q-3
from [data-model.md](./data-model.md). Verified to use indexes via
`EXPLAIN ANALYZE` assertion in a test.

### T-005 — `ContentService` orchestrator → T-001..T-004
**Files**:
- `apps/api/app/domain/content_service.py`
- `apps/api/tests/integration/test_content_service.py`

**API**:
```python
class ContentService:
    async def today(self) -> TodayResponseDTO: ...
    async def chapter(self, public_id: UUID) -> ChapterResponseDTO: ...
    async def season(self, slug: str) -> SeasonResponseDTO: ...
```

Raises `KillSwitchActive`, `NoActiveSeason`, `NoLiveChapter`,
`ChapterNotFound`, `SeasonNotFound`. Uses `SystemFlagsRepo` (from 003) for
kill-switch read.

---

## Phase 2 — HTTP endpoints (4 PRs)

### T-006 — `cache_headers.py` helper → 001-merged [P]
**Files**:
- `apps/api/app/middleware/cache_headers.py`
- `apps/api/tests/unit/test_cache_headers.py`

**API**:
```python
def set_cache(response, *, max_age: int, swr: int = 0,
              immutable: bool = False, must_revalidate: bool = False,
              no_store: bool = False) -> None: ...
def set_etag(response, etag_hex: str) -> None: ...   # adds surrounding quotes
```

### T-007 — `GET /chapters/today` → T-003, T-005, T-006
**Files**:
- `apps/api/app/api/chapters.py`
- `apps/api/tests/integration/test_chapters_today.py`
- `apps/api/tests/integration/test_etag_304.py`
- `apps/api/tests/integration/test_kill_switch_handling.py`

**Behavior**: handler maps `ContentService` exceptions to RFC 7807 responses;
sets cache headers per scenario; handles `If-None-Match`.

### T-008 — `GET /chapters/{public_id}` → T-007 [P]
**Files**:
- `apps/api/app/api/chapters.py` (extend)
- `apps/api/tests/integration/test_chapters_by_id.py`

### T-009 — `GET /seasons/{slug}` → T-005, T-006 [P]
**Files**:
- `apps/api/app/api/seasons.py`
- `apps/api/tests/integration/test_seasons_by_slug.py`

---

## Phase 3 — Contract + load tests (2 PRs)

### T-010 — Contract test → T-007..T-009
**Files**:
- `apps/api/tests/contract/test_chapters_contract.py`

**Behavior**: parses `specs/004-chapters-content/contracts/chapters.yaml`,
fires a known-good request against each endpoint, asserts response schema
matches via `openapi-schema-validator`.

### T-011 — k6 burst → T-007
**Files**:
- `scripts/k6/today_burst.js`

**Behavior**: ramp to 200 RPS over 10 s, hold 60 s. Acceptance: p95 < 500 ms,
0 5xx. Output JSON to `var/k6-report.json`.

---

## Phase 4 — PWA (3 PRs)

### T-012 — `chapter-store.ts` (Svelte 5 runes) → 002-merged, T-007
**Files**:
- `apps/web/src/lib/chapter-store.ts`
- `apps/web/tests/chapter-store.test.ts`

**API**:
```ts
export const chapterStore = {
  data: $state<TodayResponse | null>(null),
  status: $state<'idle'|'loading'|'ok'|'maintenance'|'no_season'|'error'>('idle'),
  load(): Promise<void>,
  refresh(): Promise<void>,   // SWR-style background refresh
};
```

Uses `api.ts` from module 002 (which already has the JWT/refresh interceptor).

### T-013 — `window-countdown.ts` + Svelte component → T-002, T-012 [P]
**Files**:
- `apps/web/src/lib/window-countdown.ts`
- `apps/web/src/lib/Countdown.svelte`
- `apps/web/tests/window-countdown.test.ts`

**Behavior**: pure TS function `windowFor(cycleState, windows) -> {label,
target}`. Component renders an updating countdown via `setInterval(1000)`.

### T-014 — `today.svelte` (real screen) → T-012, T-013
**Files**:
- `apps/web/src/routes/today.svelte` (replaces placeholder from 002)
- `apps/web/tests/today.test.ts`

**Behavior**: renders panel images, narration, cliffhanger, state badge,
countdown. CTA stub (final wiring in 005/007).

---

## Phase 5 — Deploy + verify (1 PR)

### T-015 — Deploy + smoke → T-007..T-014
**Files**:
- `specs/004-chapters-content/quickstart.md` (verified post-deploy)
- `specs/README.md` (mark 004 done; 005 in-progress)

**Done when**: prod `/chapters/today` returns 200 with a real R2 URL; PWA
loads in < 2 s p95 on a 3G profile.

---

## Done-when (module-level acceptance)

1. All 15 tasks merged.
2. Every box in [checklists/requirements.md](./checklists/requirements.md) ticked.
3. Prod `/chapters/today` p95 < 100 ms over a 1-hour observation window.

---

## Estimates (solo dev, calendar days)

| Phase | Tasks | Est. days |
|---|---|---|
| 0 — Pure domain | T-001..T-003 | 1.5 |
| 1 — Infra + service | T-004..T-005 | 2 |
| 2 — Endpoints | T-006..T-009 | 2 |
| 3 — Contract + load | T-010..T-011 | 1 |
| 4 — PWA | T-012..T-014 | 2.5 |
| 5 — Deploy | T-015 | 0.5 |
| **Total** | 15 tasks | **≈ 9.5 days** |

Buffer +20% → **plan for 11 working days**.
