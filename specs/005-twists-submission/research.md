# Phase 0 Research: Twist Submission

**Branch**: `005-twists-submission` | **Date**: 2026-06-07

---

## R-001 — Quota race protection

**Question**: how do we prevent two concurrent submits from both seeing `count < MAX`
and both inserting (leaking the quota)?

| Option | Pros | Cons |
|---|---|---|
| **Advisory lock per (user, chapter) (chosen)** | Cheap, scoped, releases on commit, matches module 003 pattern | Slight code overhead per request |
| `SERIALIZABLE` transaction isolation | Strong correctness | Higher conflict rate; retries on serialization failure; harder to reason about |
| Unique partial index `(user_id, chapter_id, slot_index)` with `INSERT … RETURNING` | Postgres enforces at the index level | Requires a `slot_index` column allocated by application logic — extra state |
| Optimistic INSERT then check post-hoc | Simplest | Requires rollback path; bad UX |

**Decision**: `pg_advisory_xact_lock(hashtext('twist_quota:' || user_id || ':' ||
chapter_id))` acquired at the top of the submission transaction with
`SET LOCAL lock_timeout = '1000ms'`. On timeout: 503 `lock_busy`. Released
automatically on commit/rollback.

**Why not SERIALIZABLE**: forces the application to retry on serialization failure
and increases tail latency under contention. The advisory lock is more predictable
for the operator and matches the FSM mutex pattern.

---

## R-002 — Idempotency-Key: required vs optional on submit

**Question**: on `/auth/redeem-invite` we made `Idempotency-Key` optional. Here
should it be required?

**Decision**: **required**.

**Rationale**: a user double-tapping "Tirá la idea" on a slow network would otherwise
spend their quota in one tap. The PWA generates a fresh UUID per submit attempt and
attaches the same key on retries (handled by the api.ts interceptor from module
002). Server returns 422 if missing.

**Trigger to revisit**: never; this is the right default for any user-facing
mutating endpoint.

---

## R-003 — Quota counts deleted twists (SDD inconsistency fix)

**Question**: the SDD §5.5 says deleted twists do not free quota, but the formula
written there is `MAX - count(status IN ('pending_review','approved','rejected_*'))`
which **excludes** `deleted_by_user` from the count — meaning deletes DO free quota,
contradicting the prose.

**Decision**: **the prose is correct, the formula is wrong**. This module enforces
`quota_used = COUNT(*) FROM twists WHERE user_id=? AND chapter_id=?` over ALL
statuses including `deleted_by_user`.

**Rationale**: the explicit anti-pattern the PO and the spec want to prevent is
spam-then-delete cycling, where a user submits 3, deletes 1, submits a 4th, deletes
1, etc. Counting deletes toward the quota makes the operation cost the same as
inserting a final twist — which is what "delete doesn't free quota" means
operationally.

**SDD patch proposed (to be applied at the end of module 005):**

> §5.5 "Nota" replace:
> _"La quota libre es una propiedad calculada: `MAX - count(status IN
> ('pending_review','approved','rejected_*'))`."_
>
> with:
>
> _"La quota libre es una propiedad calculada: `MAX - count(twists WHERE user_id=?
> AND chapter_id=?)` (todos los estados, incluyendo `deleted_by_user`). Esto
> implementa explícitamente la regla "borrar no libera quota"."_

---

## R-004 — DELETE response code: 200 always vs 200/204/404

**Question**: what status code does DELETE return?

| Path | Option |
|---|---|
| Twist exists, owned, deletable | **200** with body containing `deleted_at` + `remaining_submissions` |
| Twist exists but already deleted | **200** with original `deleted_at` (idempotent) |
| Twist not owned | **403** `forbidden_not_owner` |
| Twist not found at all | **404** `twist_not_found` |
| Window closed | **409** `window_closed` |
| Already filtered (status moved to approved/rejected_*) | **409** `already_filtered` |

**Decision**: as above. 200 (not 204) so the response can carry useful state for the
PWA without a second round-trip. Idempotency by returning the same body on re-DELETE.

---

## R-005 — Edit endpoint: no

**Question**: should we ship `PATCH /twists/{id}` to let users tweak typos?

**Decision**: **no**.

**Rationale**: editing creates a gaming surface — submit something innocuous, get it
through the filter, then edit it post-approval to something the filter would have
rejected. Closing this surface entirely is simpler than building edit-then-re-filter
logic. Users can delete + resubmit if they have quota left.

---

## R-006 — Forbidden_not_owner: 403 vs 404

**Question**: when user A tries to DELETE user B's twist, do we return 403 or 404?

| Option | Pros | Cons |
|---|---|---|
| **403 (chosen)** | Honest; UX clearly says "no es tuyo" | Reveals that the id exists |
| 404 | Resists enumeration | Confuses legitimate users; doesn't fit the threat model |

**Decision**: 403. The `public_id` is a UUID v4 (122 bits) — enumeration is not a
realistic threat. The honest error is better UX.

---

## R-007 — Optimistic UI in the PWA

**Question**: should the "Mis ideas" panel update immediately on submit click, or
wait for the server response?

**Decision**: **optimistic**.

**Rationale**: the modal closes instantly; the new twist appears in the panel with
a subtle "Enviando…" indicator until the response lands. On failure, the indicator
flips to an error pill with a Retry button. This UX is dramatically better on flaky
mobile connections.

**Rollback**: if the server rejects, the optimistic row is removed (or marked as
errored). The twist-store handles both transitions.

---

## R-008 — Content length: 280 inherited from Twitter convention

**Question**: why 280 chars?

**Decision**: inherits SDD §1.2 ("formato corto, máximo 280 chars"). Forces brevity
without being limiting. Configurable via `MAX_TWIST_LEN` env if the PO wants to
tighten or loosen during testing.

**Trigger to revisit**: PO feedback after the first season.

---

## R-009 — Race-test design

**Question**: how do we test the quota race?

**Decision**: integration test `test_twist_submit_race.py`:

```python
async def test_concurrent_submits_respect_quota(client, redeemed_user):
    # User has 0 twists; max = 3. Fire 10 concurrent submits.
    bodies = [{"chapter_id": str(CHAPTER_ID), "content": f"twist #{i}"}
              for i in range(10)]
    keys = [str(uuid4()) for _ in range(10)]
    results = await asyncio.gather(*[
        client.post("/api/v1/twists/submit", json=b,
                    headers={"Idempotency-Key": k,
                             "Authorization": f"Bearer {redeemed_user.jwt}"})
        for b, k in zip(bodies, keys)
    ])
    statuses = sorted([r.status_code for r in results])
    assert statuses == [201, 201, 201, 409, 409, 409, 409, 409, 409, 409]
    # DB has exactly 3 rows
    assert await count_user_twists(redeemed_user.id, CHAPTER_ID) == 3
```

The same test, run 50 times in CI with random submit delays, gives high confidence
that the advisory lock holds under realistic contention.

---

## R-010 — DELETE during filter: late-DELETE race

**Question**: what if the user clicks Delete at 17:59:59.500 and the filter cron
fires at 18:00:00.000?

**Decision**: documented as R-T5 in plan. The 18:00 transition acquires the **cycle**
advisory lock first (module 003); the DELETE acquires the **twist_quota** advisory
lock (no overlap). They don't deadlock, but the DELETE might commit milliseconds
before the filter reads `pending_review` rows.

Outcomes:
- DELETE commits first: filter sees no row → fine.
- Filter reads first then DELETE commits: DELETE sees `status != pending_review` →
  409 `already_filtered`. Fine.
- Both happen "simultaneously": SQL serialization (MVCC snapshot of the filter's read
  query) picks a winner. Either outcome is acceptable.

No DB-level race bug here.

---

## Open items

- **OQ-TW-1**: should we expose a public read of others' approved twists during
  `RECEPCION_IDEAS`? Decided: no for MVP — module 007 (voting) is the first time
  approved twists become visible.
- **OQ-TW-2**: emoji limits (a user posts 280 fire emojis)? Decided: out of scope;
  the LLM filter will likely reject as incoherent.
