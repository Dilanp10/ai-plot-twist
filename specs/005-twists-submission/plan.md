# Implementation Plan: Twist Submission, Deletion, Listing

**Branch**: `005-twists-submission` | **Date**: 2026-06-07 | **Spec**: [spec.md](./spec.md)
**Depends on**: `001-project-bootstrap`, `002-auth-invite-flow`, `003-cycle-fsm`

## Summary

Add three authenticated endpoints (`POST /twists/submit`, `DELETE
/twists/{public_id}`, `GET /me/twists`). Ship one migration (`twists` table). Wire
the JWT middleware (from 002), the cycle-state reader (from 003), and the
idempotency_keys repo (from 001). Implement a per-user-per-chapter advisory lock to
race-protect quota enforcement. Extend the PWA `today.svelte` with the submission
modal and the "Mis ideas" panel.

## Technical Context

**Languages/Versions**: same as 001–004.
**New deps**: none.
**Storage**: 1 new table (`twists`).
**Testing**: pytest + httpx + asyncio for race tests; vitest for PWA flows.
**Project type**: same.
**Performance Goals**: see NFR-001..NFR-005.
**Constraints**: no new external services; reuses module 001 idempotency table and
module 003 system_flags cache.
**Scale/Scope**: in closed beta, expected submission rate ≈ 30 inserts in a 6-h
window. Trivial load; correctness > throughput.

## Constitution Check

### Gate 1 — Zero-cost
- [x] No new services.

### Gate 2 — Idempotency
- [x] `Idempotency-Key` required on `submit` (FR-010). DELETE is naturally idempotent.
- [x] Advisory lock + quota recount under lock prevents double-spend of the quota.

### Gate 3 — TZ anchoring
- [x] `submit_until` comes from module 004's `windows` computation (UTC).
- [x] `submitted_at`, `deleted_at` are `TIMESTAMPTZ`.

### Gate 4 — Provider abstraction
- [x] N/A.

### Gate 5 — Determinism
- [x] No LLM in this module. Quota arithmetic deterministic.

### Gate 6 — Spanish UI / English code
- [x] Identifiers English. PWA strings Spanish ("Tirá una idea", "Mis ideas",
      "Borrar idea").
- [x] New domain term `twist` added to glossary.

### Gate 7 — Soft delete
- [x] **Central to this module.** Twists use `status='deleted_by_user'` +
      `deleted_at`. No `DELETE FROM twists` anywhere in this module's code.

### Gate 8 — Tests from day one
- [x] Unit: content normalization, quota arithmetic, ownership check.
- [x] Integration: happy path, window closed, over-quota, idempotency
      replay/conflict, race for last quota slot, kill-switch, ban, FAILED state,
      delete after filter.
- [x] PWA: modal renders, optimistic UI, error toasts.

### Gate 9 — Trust boundaries
- [x] JWT middleware (002) enforces auth.
- [x] Ownership check enforced server-side on DELETE; never trust client.
- [x] Idempotency-Key body hash prevents replay attack with crafted alternate body.

### Gate 10 — Observability
- [x] `twist_submitted` and `twist_deleted` log events with redacted content.

## Project Structure

### Documentation (this feature)

```text
specs/005-twists-submission/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── contracts/
│   └── twists.yaml
├── quickstart.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### New / modified code

```text
apps/api/
├── alembic/versions/
│   └── 0007_twists.py                      ← NEW
├── app/
│   ├── domain/
│   │   ├── twist_content.py                ← NEW (normalization)
│   │   ├── twist_quota.py                  ← NEW (count + remaining)
│   │   └── twist_submission.py             ← NEW (orchestrator service)
│   ├── infra/
│   │   └── twists_repo.py                  ← NEW
│   ├── api/
│   │   ├── twists.py                       ← NEW (submit, delete)
│   │   └── me_twists.py                    ← NEW (list)
│   └── (middleware unchanged)
└── tests/
    ├── unit/
    │   ├── test_twist_content.py
    │   └── test_twist_quota.py
    └── integration/
        ├── test_twist_submit_happy.py
        ├── test_twist_submit_window_closed.py
        ├── test_twist_submit_quota.py
        ├── test_twist_submit_idempotency.py
        ├── test_twist_submit_race.py       ← 10 concurrent submits
        ├── test_twist_delete.py
        ├── test_twist_delete_after_filter.py
        └── test_me_twists.py

apps/web/
├── src/
│   ├── routes/
│   │   └── today.svelte                    ← MODIFIED (add CTA)
│   ├── lib/
│   │   ├── twist-store.ts                  ← NEW
│   │   ├── twist-api.ts                    ← NEW (typed wrappers)
│   │   └── components/
│   │       ├── TwistModal.svelte           ← NEW
│   │       └── MyTwistsPanel.svelte        ← NEW
└── tests/
    ├── twist-store.test.ts
    ├── twist-modal.test.ts
    └── my-twists-panel.test.ts
```

## Phase 0 — Research

See [research.md](./research.md). Key decisions:

- **Advisory lock vs SERIALIZABLE isolation** for quota race protection.
- **Idempotency-Key REQUIRED on submit** (vs optional like auth/redeem-invite).
- **Quota counts deleted twists** (corrects SDD §5.5 inconsistency).
- **DELETE response always 200** (idempotent, even for already-deleted).
- **No edit endpoint** (PATCH explicitly excluded).
- **403 vs 404 on cross-user DELETE** (we picked 403).
- **Optimistic UI in PWA** (vs wait-and-confirm).

## Phase 1 — Design Artefacts

- [contracts/twists.yaml](./contracts/twists.yaml).
- [data-model.md](./data-model.md).
- [quickstart.md](./quickstart.md).
- [checklists/requirements.md](./checklists/requirements.md).
- [tasks.md](./tasks.md).

## Phase 2 — Implementation Sequence

1. **T-001** — Migration `0007_twists.py`.
2. **T-002..T-004** — Pure domain: `twist_content`, `twist_quota`,
   value objects.
3. **T-005..T-006** — `TwistsRepo` + service `TwistSubmissionService`.
4. **T-007..T-009** — Endpoints: submit, delete, list.
5. **T-010..T-013** — PWA: store, modal, panel, today screen integration.
6. **T-014..T-015** — Race + chaos tests.
7. **T-016** — Deploy + smoke.

## Risks & Mitigations (feature-local)

| ID | Risk | Mitigation |
|---|---|---|
| **R-T1** | Quota race leaks via concurrent submits | Advisory lock per (user, chapter) — see FR-005. Test T-014 fires 10 concurrent submits. |
| **R-T2** | Idempotency-Key conflict surfaces as confusing error | UX in PWA: surface "Ya enviaste esta idea" with explicit copy. |
| **R-T3** | User accidentally double-taps Submit on a slow network | The PWA generates the Idempotency-Key client-side per submission attempt; retries reuse the key automatically. |
| **R-T4** | Content normalization strips meaningful characters (emojis) | Emojis are in `So` Unicode category, NOT `Cc`; preserved. Test fixtures cover. |
| **R-T5** | DELETE permitted up to the last millisecond before 18:00, then filter sees it as `pending_review` and processes a "ghost" twist | The filter (module 006) reads `pending_review` rows; this DELETE-then-FILTER race is bounded by 18:00 cron jitter. Acceptable. |
| **R-T6** | SDD formula in §5.5 contradicts FR-004 | Documented in research R-003; SDD patch proposed at the end of this module. |

## Post-Conditions

After merge:
- Authenticated users can contribute twists during the 6-h window.
- The `twists` table is populated, ready for module 006 (filter) to transition statuses
  and for module 007 (voting) to read approved twists.
- The PWA has a working "Mis ideas" view that becomes more informative once 006
  ships (rejections + reasons appear).
