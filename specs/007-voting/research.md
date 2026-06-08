# Phase 0 Research: Voting

**Branch**: `007-voting` | **Date**: 2026-06-07

---

## R-001 — Per-user stable random sort

**Question**: how do we shuffle the vote-feed so that (a) each user sees a different
order (fairness — no top-of-list bias), (b) the order is stable for a given user
across refreshes (no refresh-gaming)?

| Option | Pros | Cons |
|---|---|---|
| Truly random (`ORDER BY random()`) | Fair, simple | Refresh-game: a user keeps refreshing to push their favorite to the top |
| **Stable per-user random (chosen)** | Fair AND stable | Slightly more code; non-trivial to compute in SQL portably |
| Stable global random (single seed per cycle) | Everyone sees the same order | Position 1 is over-voted (the cohort all sees it first) |
| `ORDER BY public_id` | Trivial | Same order for everyone → top-of-list bias |

**Decision**: **stable per-user**. The seed is `sha256_int(f"{cycle_id}:{user_id}")`.

**SQL pattern**: we can't easily inject a per-row stable random key into Postgres
sort. Two workable approaches:

| Approach | How |
|---|---|
| Compute in Python | `SELECT * FROM twists WHERE …` → Python `random.Random(seed).shuffle(rows)` |
| Compute in SQL with `hashtext` | `ORDER BY hashtext(public_id::text || :seed_salt::text)` |

**Pick**: **Python-side shuffle**. The result set is ≤ ~200 twists per chapter
(closed beta scale). Python shuffle is O(n), deterministic given a `random.Random`
seeded with the integer, and lets us encode cursor positions as integer offsets
into the already-shuffled list. Trade-off: we materialize the full list into memory
per request (acceptable: ≤ 200 rows × ~300 bytes ≈ 60 KB).

The cursor (R-002) stores `last_position` (an integer index in the shuffled list).

**Trigger to revisit**: > 1000 approved twists per chapter (won't happen in MVP).
Then move to a SQL `ORDER BY hashtext(...)` approach.

---

## R-002 — Cursor format

**Question**: opaque cursor encoded how?

**Decision**: base64-url of compact JSON:

```json
{"s":"random","p":42,"v":null}
```

Where:
- `s` = sort (random | recent | hot).
- `p` = `last_position` (the index in the sorted list of the last item returned).
- `v` = `last_sort_value` (only meaningful for `recent` / `hot` to disambiguate ties).

The server decodes, validates `s` matches the current query's `sort` (otherwise 422
`cursor_invalid`), and resumes.

**Why JSON and not a hash**: cursors are not security tokens. Transparency at debug
time outweighs obfuscation. Clients are not expected to interpret them, but a
developer can.

---

## R-003 — Quota race protection

**Question**: same problem as module 005 — concurrent votes from the same user can
exceed `MAX_VOTES_PER_USER_PER_CHAPTER`.

**Decision**: same pattern as 005 — `pg_advisory_xact_lock(hashtext('vote_quota:'
|| user_id || ':' || chapter_id))` acquired with 1 s timeout. The lock is
**user-scoped**, so it does not interfere with other users voting simultaneously
on the same chapter.

**On double-vote of the same twist by same user**: handled separately by the UNIQUE
constraint + `ON CONFLICT DO NOTHING`, OUTSIDE the advisory lock. This means the
two race patterns are addressed by independent primitives:

| Race | Primitive |
|---|---|
| Same user, different twists, hitting quota | Advisory lock |
| Same user, same twist | UNIQUE `(twist_id, user_id)` + ON CONFLICT |
| Different users, same twist | No problem; concurrent inserts of distinct keys |

---

## R-004 — Self-vote: allow or deny?

**Question**: should users be allowed to vote for their own twists?

| Option | Pros | Cons |
|---|---|---|
| **Allow (default, chosen)** | Closed-beta family-friends: people can endorse their own ideas; UX is consistent | A user with 3 twists + 5 votes can guarantee 3 of their twists get 1 vote, slightly tilting outcomes |
| Deny | More "fair" in tournament sense | Surprising UX; small cohort makes it sting more |

**Decision**: **allow by default**, but ship an env flag `ALLOW_SELF_VOTE` so the
PO can switch behavior season-to-season without a code change.

**Rationale**: in a 10–30 user cohort, a strict no-self-vote rule means every
twist needs at least one external endorsement to count. That's not necessarily
bad, but it creates social friction ("Lucia, can you vote for my idea?"). The
permissive default reduces friction; if the PO sees abuse, flip the flag.

---

## R-005 — `chapter_id` denormalization on `votes`

**Question**: the SDD already has `votes.chapter_id` as a denormalized column. Why?

**Answer**: it lets the per-chapter quota count run as a 1-index lookup:

```sql
SELECT COUNT(*) FROM votes WHERE user_id = ? AND chapter_id = ?;
```

Without the denorm, we'd need `votes JOIN twists` to filter by chapter — N+1ish at
scale. The denorm cost is one extra column write per vote and a server-side
invariant (`votes.chapter_id == twists.chapter_id`). The latter is verified at
INSERT time in the service layer.

**Verdict**: keep the denorm exactly as the SDD specifies.

---

## R-006 — `has_my_vote` JOIN cost

**Question**: every feed query joins against `votes` to set `has_my_vote`. Is that
acceptable?

**Decision**: yes.

**Cost analysis**: the user has ≤ `MAX_VOTES_PER_USER_PER_CHAPTER` (5) rows in
`votes` for the current chapter. We materialize them into a Python `set` once
per request and check membership during serialization. No SQL JOIN needed:

```python
my_voted_twist_ids = {row.twist_id for row in
    votes_repo.list_for_user_chapter(user_id, chapter_id)}
# In response shaping:
item.has_my_vote = (twist.id in my_voted_twist_ids)
```

Single PK-index lookup, one round-trip.

---

## R-007 — Optimistic UI on vote

**Question**: same UX question as twist submission. Vote first, confirm later, or
wait?

**Decision**: **optimistic**. The PWA increments `vote_count` and toggles
`has_my_vote` instantly; on 409 it rolls back and shows a toast. Vote-store has a
small state machine per-twist:

```
idle → optimistic → confirmed (server returned 200)
     → optimistic → reverted (server returned 4xx)
```

Concurrent taps on the same twist are debounced at 300 ms.

---

## R-008 — Cursor pagination on `random` sort

**Question**: cursor on a Python-side shuffle is just "next offset". Is that safe?

**Answer**: yes, because the seed is stable. The same user, same cycle, same query
→ identical shuffle order → cursor positions are stable.

**Risk**: if the underlying twist set changes (e.g., the PO runs `rerun-filter`
and `rejected_offensive` becomes `approved` after a manual fix), the shuffle
includes new entries and offsets shift. Resolution: client gets 200 OK with the
new list AND `total_approved` mismatch. The PWA can detect and re-shuffle from
position 0 if it wants tight consistency. Documented as a known limitation; not a
correctness bug, just a UX glitch in a rare ops scenario.

---

## R-009 — Idempotency on vote

**Question**: do we require `Idempotency-Key` on `POST /vote`?

**Decision**: **no, optional**. The UNIQUE constraint makes votes naturally
idempotent. Honor `Idempotency-Key` if sent (returns cached body) but don't
require it.

**Why not require**: the operation is uniquely identified by `(twist_id, user_id)`.
A client retry under that contract is safe. Requiring an extra header is
ceremony without value.

---

## R-010 — Real-time updates (SSE / WebSocket)?

**Question**: should the feed update vote counts in real-time?

**Decision**: **no, MVP polls**. The PWA's vote screen does a foreground refresh
every 30 s (configurable). Real-time updates require WebSocket or SSE infra,
which violates Gate 1 if it requires Redis/pub-sub and complicates Fly machine
lifecycle.

**Trigger to revisit**: post-launch UX feedback. Cloudflare's free WebSocket on
Workers could pay this cost without infra changes.

---

## Open items

- **OQ-V-1**: should votes count toward a user's reputation / status? Out of MVP.
- **OQ-V-2**: anti-collusion (groups coordinating votes)? Not addressable
  algorithmically in closed beta of 10–30 family members. Treat as social.
- **OQ-V-3**: surface vote_count percentiles ("tu idea está en el top 20 %")?
  Future analytics module.
