# Implementation Plan: Voting

**Branch**: `007-voting` | **Date**: 2026-06-07 | **Spec**: [spec.md](./spec.md)
**Depends on**: `002-auth-invite-flow`, `003-cycle-fsm`, `005-twists-submission`,
                `006-directors-filter`

## Summary

Two authenticated endpoints (`GET /twists/vote-feed`, `POST /twists/vote`), one new
table (`votes`), per-user stable sort with cursor pagination, advisory-locked
quota check, atomic UPSERT via UNIQUE constraint. PWA vote screen with optimistic
UI. No LLM calls. Reads approved twists produced by module 006.

## Technical Context

**Languages/Versions**: same as 001–006.
**New deps**: none.
**Storage**: 1 new table (`votes`).
**Testing**: pytest + asyncio for race; vitest for PWA.
**Project type**: same.
**Performance Goals**: see NFR-001..NFR-004.
**Constraints**: no Redis (advisory lock + ON CONFLICT in PG handles atomicity).
**Scale/Scope**: closed beta peak load: ~30 users × 5 votes = 150 vote inserts in
a 5-hour window. Trivial.

## Constitution Check

### Gate 1 — Zero-cost
- [x] No new services.

### Gate 2 — Idempotency
- [x] `votes` UNIQUE `(twist_id, user_id)` + `ON CONFLICT DO NOTHING` provides
      natural idempotency. Re-fire of the same POST is a no-op (with consistent
      200 response).
- [x] `vote-feed` is a pure GET.

### Gate 3 — TZ anchoring
- [x] `vote_until` comes from module 004's windows; no time-of-day arithmetic
      here.

### Gate 4 — Provider abstraction
- [x] N/A (no LLM/T2I).

### Gate 5 — Determinism
- [x] Per-user random sort is deterministic on `hash(cycle_id, user_id)`.
      Reproducibility test: same input → same order.
- [x] Tiebreak rules for `hot` sort are documented and tested.

### Gate 6 — Spanish / English
- [x] Identifiers English. PWA strings Spanish ("Tu voto cuenta", "Te quedan N
      votos", "Ya votaste esta idea").

### Gate 7 — Soft delete
- [x] Votes for soft-deleted twists are not creatable (the feed excludes them).
      If a user already voted and the twist later transitions to
      `deleted_by_user` (shouldn't happen post-VOTACION but defensive), the
      vote row remains but the feed hides the twist.

### Gate 8 — Tests from day one
- [x] Unit: sort seed determinism, cursor encode/decode, quota arithmetic.
- [x] Integration: happy path, double-vote, over-quota, self-vote allow/deny,
      race for last quota slot, race for same-twist double-vote, kill-switch,
      banned, window-closed.
- [x] PWA: optimistic UI, error toasts, "Mis votos" indicator.

### Gate 9 — Trust boundaries
- [x] JWT enforced. `user_id` from JWT, never from request body.
- [x] `chapter_id` derived server-side from twist lookup; client cannot specify
      it.
- [x] Cursor is opaque (base64 of JSON); server parses defensively.

### Gate 10 — Observability
- [x] `vote_cast {outcome}` logged on every attempt (happy + every named error).

## Project Structure

```text
specs/007-voting/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── contracts/
│   └── voting.yaml
├── quickstart.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

```text
apps/api/
├── alembic/versions/
│   └── 0008_votes.py                     ← NEW
├── app/
│   ├── domain/
│   │   ├── vote_sort.py                  ← NEW (seed math, tiebreak)
│   │   ├── vote_cursor.py                ← NEW (encode/decode)
│   │   └── vote_service.py               ← NEW (orchestrator)
│   ├── infra/
│   │   └── votes_repo.py                 ← NEW
│   └── api/
│       └── voting.py                     ← NEW (2 routes)
└── tests/
    ├── unit/
    │   ├── test_vote_sort.py
    │   └── test_vote_cursor.py
    └── integration/
        ├── test_vote_feed.py
        ├── test_vote_cast_happy.py
        ├── test_vote_cast_double.py
        ├── test_vote_cast_over_quota.py
        ├── test_vote_cast_self_vote.py
        ├── test_vote_window_closed.py
        ├── test_vote_race_same_twist.py
        ├── test_vote_race_quota_edge.py
        └── test_vote_feed_cursor.py

apps/web/
├── src/
│   ├── routes/
│   │   └── vote.svelte                   ← NEW
│   ├── lib/
│   │   ├── vote-store.ts                 ← NEW
│   │   ├── vote-api.ts                   ← NEW
│   │   └── components/
│   │       ├── VoteCard.svelte           ← NEW
│   │       └── MyVotesIndicator.svelte   ← NEW
└── tests/
    ├── vote-store.test.ts
    └── vote-card.test.ts
```

## Phase 0 — Research

See [research.md](./research.md). Key decisions:

- Per-user stable random sort via `hash(cycle_id, user_id)`.
- Cursor pagination encoded as base64 JSON `{sort, last_position, sort_value}`.
- Advisory lock for quota edge; UNIQUE constraint for double-vote.
- `ALLOW_SELF_VOTE=true` default (closed-beta friendly).
- Optimistic UI on PWA with rollback on 409.
- No `Idempotency-Key` required (UNIQUE makes votes naturally idempotent).
- `chapter_id` denormalized on `votes` for fast quota counting.

## Phase 1 — Design Artefacts

- [contracts/voting.yaml](./contracts/voting.yaml).
- [data-model.md](./data-model.md).
- [quickstart.md](./quickstart.md).
- [checklists/requirements.md](./checklists/requirements.md).
- [tasks.md](./tasks.md).

## Phase 2 — Implementation Sequence

1. **T-001** — Migration `0008_votes.py`.
2. **T-002..T-003** — Pure domain: `vote_sort`, `vote_cursor`.
3. **T-004** — `VotesRepo`.
4. **T-005** — `VoteService` orchestrator.
5. **T-006..T-007** — Endpoints: vote-feed, vote-cast.
6. **T-008** — Race tests.
7. **T-009..T-012** — PWA: store, api, card, screen.
8. **T-013** — Deploy + observe.

## Risks & Mitigations (feature-local)

| ID | Risk | Mitigation |
|---|---|---|
| **R-V1** | Quota race leaks | Advisory lock per (user, chapter) — same pattern as 005. |
| **R-V2** | Stale `vote_count` in feed | Acceptable; closed beta. Update on next poll. |
| **R-V3** | Cursor mismatch on sort change | Cursor encodes sort; client gets 422 cursor_invalid and starts over. |
| **R-V4** | "Mis votos" UI desync after error | Vote-store re-fetches on error. |
| **R-V5** | Random sort produces consistent "winner-prone" order across users | Each user has their own seed; outcome is statistically uniform across the cohort. |

## Post-Conditions

After merge:
- Users can vote during `VOTACION`.
- The `votes` table is populated and ready for module 008's winner-selection
  query.
- The PWA has a working vote screen.
