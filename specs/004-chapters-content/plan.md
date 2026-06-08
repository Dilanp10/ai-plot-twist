# Implementation Plan: Chapter Content Read API

**Branch**: `004-chapters-content` | **Date**: 2026-06-07 | **Spec**: [spec.md](./spec.md)
**Depends on**: `001-project-bootstrap`, `003-cycle-fsm`

## Summary

Add three read-only endpoints (`/chapters/today`, `/chapters/{id}`, `/seasons/{slug}`).
Build a `ChapterContentService` that joins `cycles + chapters + seasons` in a single
query, computes the four window timestamps, redacts the bible, and renders the JSON
response. Wire HTTP caching headers and ETag-based conditional GETs. Honor the
kill-switch and the `no_active_season` edge case. No DB writes, no new tables.

## Technical Context

**Languages/Versions**: same as 001–003.
**New deps**: none.
**Storage**: read-only — `seasons`, `chapters`, `cycles`, `system_flags`.
**Testing**: pytest + httpx; one snapshot test per response shape; integration test
against ephemeral PG with seeded chapter.
**Project type**: same.
**Performance Goals**: see NFR-001..NFR-004.
**Constraints**: zero asset proxy; backend never touches R2 bytes. CORS allow-list
must include the Cloudflare Pages origin.
**Scale/Scope**: this is the heaviest-RPS endpoint of the MVP. 12:00 PM premiere is
a thundering-herd moment from the closed beta cohort.

## Constitution Check

### Gate 1 — Zero-cost
- [x] No new services. R2 free tier handles asset egress.

### Gate 2 — Idempotency
- [x] All endpoints are pure GETs.

### Gate 3 — TZ anchoring
- [x] Window timestamps computed in UTC for transport, derived from
      `state_entered_at + dwell` in UTC arithmetic. The PWA renders in ART for the
      user. `cycle_date` is exposed as a `date` (no time component) and is the ART
      calendar date.

### Gate 4 — Provider abstraction
- [x] N/A.

### Gate 5 — Determinism
- [x] Same `(cycle, chapter, kill_switch)` → same response → same ETag.

### Gate 6 — Spanish UI / English code
- [x] Identifiers English. Error `detail` strings in Spanish (user-visible via the
      PWA error UI).

### Gate 7 — Soft delete
- [x] N/A.

### Gate 8 — Tests from day one
- [x] One unit test per response shape; one integration test per endpoint;
      contract test asserting `response.json()` validates against
      `contracts/chapters.yaml`.
- [x] Cache-header tests.
- [x] ETag/304 tests.

### Gate 9 — Trust boundaries
- [x] All endpoints are unauthenticated. No PII in responses.
- [x] Bible redaction is the only sensitive surface: an allowlist of top-level
      keys, tested.

### Gate 10 — Observability
- [x] `content_read` log on every request with the documented keys.

## Project Structure

### Documentation (this feature)

```text
specs/004-chapters-content/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── contracts/
│   └── chapters.yaml
├── quickstart.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### New / modified code

```text
apps/api/
├── app/
│   ├── domain/
│   │   ├── bible_redaction.py            ← NEW (allowlist filter)
│   │   ├── windows.py                    ← NEW (compute submit_until/etc.)
│   │   ├── etag.py                       ← NEW (ETag derivation)
│   │   └── content_service.py            ← NEW (orchestrator)
│   ├── api/
│   │   ├── chapters.py                   ← NEW (2 routes)
│   │   └── seasons.py                    ← NEW (1 route)
│   ├── middleware/
│   │   └── cache_headers.py              ← NEW (helper)
│   └── infra/
│       └── content_repo.py               ← NEW (joined read)
└── tests/
    ├── unit/
    │   ├── test_bible_redaction.py
    │   ├── test_windows.py
    │   └── test_etag.py
    ├── integration/
    │   ├── test_chapters_today.py
    │   ├── test_chapters_by_id.py
    │   ├── test_seasons_by_slug.py
    │   ├── test_kill_switch_handling.py
    │   └── test_etag_304.py
    └── contract/
        └── test_chapters_contract.py     ← Schemathesis or hand-rolled

apps/web/
├── src/
│   ├── routes/
│   │   └── today.svelte                  ← REPLACES placeholder from 002
│   ├── lib/
│   │   ├── chapter-store.ts              ← NEW
│   │   └── window-countdown.ts           ← NEW
│   └── App.svelte                        ← updated routing
└── tests/
    ├── today.test.ts
    └── window-countdown.test.ts
```

## Phase 0 — Research

See [research.md](./research.md). Key decisions:

- **R2 access model: public bucket** (no presigned URLs in MVP).
- **Caching: layered** — `Cache-Control` + ETag + service worker stale-while-revalidate.
- **Single SQL query** joining `cycles ⨝ chapters ⨝ seasons` rather than N+1 ORM.
- **Window timestamps**: server-computed, exposed in UTC; PWA renders ART.
- **Bible redaction**: top-level key allowlist (declarative + tested).
- **Error shape**: RFC 7807 problem details; `code` is the stable machine identifier.

## Phase 1 — Design Artefacts

- [contracts/chapters.yaml](./contracts/chapters.yaml) — full OpenAPI 3.1.
- [data-model.md](./data-model.md) — no new tables; documents read queries and indexes
  the queries depend on.
- [quickstart.md](./quickstart.md) — exercise each endpoint, see the PWA render.
- [checklists/requirements.md](./checklists/requirements.md).
- [tasks.md](./tasks.md).

## Phase 2 — Implementation Sequence

1. **T-001..T-003** — Pure domain: `bible_redaction`, `windows`, `etag`.
2. **T-004** — `ContentRepo` joined read.
3. **T-005** — `ContentService` orchestrator.
4. **T-006..T-008** — Three endpoints.
5. **T-009** — Cache header middleware helper.
6. **T-010..T-011** — PWA today screen + countdown component.
7. **T-012** — Contract test.
8. **T-013** — Load test with k6 (verify NFR-004).

## Risks & Mitigations (feature-local)

| ID | Risk | Mitigation |
|---|---|---|
| **R-CH1** | 12:00 PM premiere burst overwhelms the Fly machine | Cache hit ratio at the SW + Cloudflare edge layers absorbs most. Backend p95 budgeted at 100 ms. |
| **R-CH2** | Bible redaction misses a sensitive key added later | The allowlist is the source of truth; any new top-level key is opt-in. Unit test asserts the redaction never adds. |
| **R-CH3** | ETag includes mutable cycle state → clients churn | Documented: ETag flips at every state transition (4 times per day). Acceptable; cache lifetimes are short for `today`. |
| **R-CH4** | Manifest has broken R2 URLs (module 008 bug) | PWA surfaces panel-level errors; backend does not validate URLs (out of scope). |
| **R-CH5** | CORS blocks the Pages origin in prod | Allow-list configured via `ALLOWED_ORIGINS` env var; tested in CI. |

## Post-Conditions

After merge:
- The PWA renders a real chapter, not a placeholder.
- All future feature modules (005 twists, 007 voting, 011 push) can rely on
  `cycle_state` and `windows` being exposed in the today response.
- The k6 load profile shows ≥ 200 RPS for 60 s with p95 < 500 ms (G-5).
