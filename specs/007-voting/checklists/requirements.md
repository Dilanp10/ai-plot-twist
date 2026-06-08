# Requirements Checklist: Voting

**Branch**: `007-voting` | **Date**: 2026-06-07

---

## Functional Requirements

- [ ] **FR-001** — `GET /twists/vote-feed` requires JWT; returns approved twists
      for the **live** chapter (not past).
- [ ] **FR-002** — Default sort `random` with seed `sha256_int(cycle_id+user_id)`;
      `recent` and `hot` available; cursor encodes `(sort, last_position,
      sort_value)`.
- [ ] **FR-003** — `limit` default 25, max 100. Response includes
      `page.next_cursor` (null at end) and `page.total_approved`.
- [ ] **FR-004** — `has_my_vote` computed via Python set membership against the
      user's own votes (bounded by quota of 5).
- [ ] **FR-005** — `POST /twists/vote` uses `INSERT … ON CONFLICT DO NOTHING`.
      0 rows affected → 409 `already_voted`.
- [ ] **FR-006** — Advisory lock `vote_quota:<user>:<chapter>` before count +
      insert. 1 s timeout → 503 `lock_busy`.
- [ ] **FR-007** — Success response includes `new_vote_count` and full `user_quota`.
- [ ] **FR-008** — Both endpoints validate `cycle.state == 'VOTACION'` AND
      `now() < vote_until`.
- [ ] **FR-009** — `ALLOW_SELF_VOTE` flag honored; default `true`.
- [ ] **FR-010** — `Idempotency-Key` optional on vote; UNIQUE makes vote
      naturally idempotent.
- [ ] **FR-011** — Kill-switch / banned / chapter-mismatch handled per pattern.
- [ ] **FR-012** — `vote_cast {outcome, new_vote_count}` log on every attempt.
- [ ] **FR-013** — PWA: `/vote` route, vote cards, "Mis votos" indicator,
      optimistic UI + rollback. Screenshot in PR.

## Non-Functional Requirements

- [ ] **NFR-001** — `GET /vote-feed` p95 < 150 ms with 100 approved twists.
- [ ] **NFR-002** — `POST /vote` p95 < 200 ms.
- [ ] **NFR-003** — 20 concurrent voters × 5 votes → 100 inserts, 0 5xx.
- [ ] **NFR-004** — 10 concurrent votes same user same twist → exactly 1 inserted.

## Constitution Gates

- [ ] **Gate 1 — Zero-cost** — No new services.
- [ ] **Gate 2 — Idempotency** — UNIQUE + ON CONFLICT.
- [ ] **Gate 3 — TZ anchoring** — `vote_until` from module 004.
- [ ] **Gate 4 — Provider abstraction** — N/A.
- [ ] **Gate 5 — Determinism** — Stable per-user sort, tiebreak documented.
- [ ] **Gate 6 — Spanish UI / English code** — Strings Spanish; code English.
- [ ] **Gate 7 — Soft delete** — Deleted twists never appear in feed.
- [ ] **Gate 8 — Tests from day one** — Race + idempotency + sort + cursor +
      window all tested.
- [ ] **Gate 9 — Trust boundaries** — `user_id` from JWT; `chapter_id`
      derived server-side; cursor parsed defensively.
- [ ] **Gate 10 — Observability** — `vote_cast` event with all outcomes.

## Documentation

- [ ] Quickstart walked end-to-end.
- [ ] `specs/README.md` marks `done`; 008 `in-progress`.

## Sign-off

- [ ] Reviewer 1 (engineering)
- [ ] Reviewer 2 (PO)
