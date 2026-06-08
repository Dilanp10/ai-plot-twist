# Requirements Checklist: Twist Submission

**Branch**: `005-twists-submission` | **Date**: 2026-06-07

---

## Functional Requirements

- [ ] **FR-001** — `POST /twists/submit` requires JWT and `Idempotency-Key` header.
      `test_twist_submit_happy.py::test_jwt_required`,
      `::test_idempotency_key_required`.
- [ ] **FR-002** — Validation order honored. Specific tests per error code:
      `test_kill_switch`, `test_bad_shape`, `test_chapter_mismatch`,
      `test_window_closed`, `test_over_quota`.
- [ ] **FR-003** — Content normalization: NFKC + Cc-strip + length bounds. Unit
      tests cover RTL overrides, zero-width chars, emojis (preserved), whitespace-
      only (rejected).
- [ ] **FR-004** — Quota counts ALL twists including `deleted_by_user`. Verified by
      `test_twist_submit_quota.py::test_delete_does_not_free_quota`.
- [ ] **FR-005** — Advisory lock `twist_quota:<user>:<chapter>` acquired with 1 s
      timeout. Race test asserts 10 concurrent submits → exactly MAX succeed.
      `test_twist_submit_race.py`.
- [ ] **FR-006** — New twists land with `status='pending_review'`. Verified by DB
      assertion after submit.
- [ ] **FR-007** — DELETE: ownership + window + status checks. Five named
      paths tested: happy, already-deleted-idempotent, cross-user-403,
      window-closed-409, already-filtered-409.
- [ ] **FR-008** — `GET /me/twists` returns user's twists with quota object.
      `test_me_twists.py` covers empty list, full quota, mix of statuses.
- [ ] **FR-009** — `remaining_submissions` correctly computed and never decreases
      across consecutive deletes (FR-004).
- [ ] **FR-010** — Idempotency: same key + same body → 200 with cached body; same
      key + different body → 409 `idempotency_conflict`.
- [ ] **FR-011** — Structured log events `twist_submitted` and `twist_deleted` with
      content truncated to 20 chars + `…`. Grep test asserts no full content in
      logs.
- [ ] **FR-012** — PWA flows: CTA renders only in `RECEPCION_IDEAS`; modal opens;
      optimistic UI works; quota chip updates. Visual screenshot in PR.

## Non-Functional Requirements

- [ ] **NFR-001** — `/twists/submit` p95 < 250 ms.
- [ ] **NFR-002** — `DELETE /twists/{id}` p95 < 150 ms.
- [ ] **NFR-003** — `/me/twists` p95 < 100 ms.
- [ ] **NFR-004** — 100 concurrent submits across distinct users with 0 5xx.
- [ ] **NFR-005** — 10 concurrent submits for same (user, chapter) exact
      `min(10, MAX)` succeed, no 5xx, no deadlock. CI runs the race test 50
      times.

## Constitution Gates

- [ ] **Gate 1 — Zero-cost** — No new services.
- [ ] **Gate 2 — Idempotency** — Submit requires `Idempotency-Key`; DELETE
      naturally idempotent.
- [ ] **Gate 3 — TZ anchoring** — All timestamps `TIMESTAMPTZ`. Window edge
      tests use both UTC and ART.
- [ ] **Gate 4 — Provider abstraction** — N/A.
- [ ] **Gate 5 — Determinism** — Quota arithmetic deterministic; no
      randomness.
- [ ] **Gate 6 — Spanish UI / English code** — Identifiers English; user-
      facing strings Spanish; glossary updated with `twist`.
- [ ] **Gate 7 — Soft delete** — `deleted_by_user` status + `deleted_at`
      column. No `DELETE FROM twists` anywhere.
- [ ] **Gate 8 — Tests from day one** — Race test, idempotency test,
      normalization, ownership all ship in PR.
- [ ] **Gate 9 — Trust boundaries** — JWT enforced; ownership server-checked;
      idempotency body hash prevents body swap.
- [ ] **Gate 10 — Observability** — `twist_submitted`, `twist_deleted` events
      emitted.

## Documentation

- [ ] Quickstart walked end-to-end on a clean dev box.
- [ ] `specs/README.md` marks module `done`; marks 006 `in-progress`.
- [ ] SDD patch (per research R-003) applied or PR-staged.

## Sign-off

- [ ] Reviewer 1 (engineering)
- [ ] Reviewer 2 (PO)
