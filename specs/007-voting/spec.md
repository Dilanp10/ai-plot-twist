# Feature Specification: Voting on Approved Twists

**Feature Branch**: `007-voting`
**Created**: 2026-06-07
**Status**: Draft
**Depends on**: `002-auth-invite-flow`, `003-cycle-fsm`, `005-twists-submission`,
                `006-directors-filter`

## Summary

Two authenticated endpoints (`GET /twists/vote-feed`, `POST /twists/vote`) that
expose the chapter's `approved` twists for voting and persist atomic, deduplicated
votes. Active only during `VOTACION`. The feed uses a **per-user stable random sort**
derived from `hash(cycle_id, user_id)` so users can't refresh-game their way to a
different order. Vote quota: `MAX_VOTES_PER_USER_PER_CHAPTER` (default 5). Race-safe
via UNIQUE constraint + advisory lock for the quota counter.

PWA: a vote screen that swipes through twists (or shows a list — final UX shape
decided in T-014), with optimistic UI on each vote.

## User Scenarios & Testing

### User Story 1 — User opens vote-feed and casts votes (Priority: P1)

A user opens the PWA at 19:30 ART. The cycle is in `VOTACION`. They see a shuffled
list of approved twists and tap "👍" on three of them.

**Why this priority**: voting is what produces the winning twist, which seeds the
next chapter. No vote = no generation = no next chapter.

**Independent Test**: bootstrap, populate ~10 `approved` twists (manually or via
the filter), force `VOTACION`, redeem an invite. Call `GET /vote-feed`, assert
shape; POST 3 votes; assert each twist's `vote_count` incremented, user's
`quota.used = 3`.

**Acceptance Scenarios**:

1. **Given** the cycle is in `VOTACION` and 10 approved twists exist,
   **When** the user calls `GET /api/v1/twists/vote-feed`,
   **Then** HTTP 200 with `{items: [...10 entries...], page: {next_cursor: null,
   limit: 25, total_approved: 10}, user_quota: {used: 0, remaining: 5}}`. Each
   item has `id, content, vote_count, has_my_vote`. Items are sorted by a stable
   per-user seed.

2. **Given** the same call repeated within the same session,
   **When** the user calls `GET /vote-feed` again,
   **Then** the order of items is identical (stable seed).

3. **Given** the user has voted for twist T,
   **When** they call `GET /vote-feed`,
   **Then** the entry for T has `has_my_vote: true` and `vote_count` reflects the
   new total.

4. **Given** a valid twist id and the user has not voted for it yet,
   **When** they POST `{twist_id}` to `/twists/vote`,
   **Then** HTTP 200 with `{twist_id, new_vote_count, user_quota: {used: <+1>,
   remaining: <-1>}}`. DB has one new `votes` row.

### User Story 2 — Quota and double-vote enforcement (Priority: P1)

The system MUST reject double votes and quota overruns under all conditions.

**Acceptance Scenarios**:

1. **Given** the user already voted for twist T,
   **When** they POST `{twist_id: T}` again,
   **Then** HTTP 409 `{"code":"already_voted","twist_id":T}`. DB unchanged.

2. **Given** the user has 5 votes for the current chapter,
   **When** they POST a 6th vote (different twist),
   **Then** HTTP 409 `{"code":"over_quota","quota_used":5,"quota_max":5}`.

3. **Given** the user has 4 votes and fires 2 concurrent votes for different
   twists,
   **When** both arrive within 50 ms,
   **Then** exactly one succeeds (HTTP 200), the other gets `over_quota`. DB count
   = 5.

### User Story 3 — Window enforcement (Priority: P1)

Vote-feed and vote-cast are tightly gated to `VOTACION` state.

**Acceptance Scenarios**:

1. **Given** `cycle.state != 'VOTACION'`,
   **When** the user calls `GET /vote-feed` OR `POST /vote`,
   **Then** HTTP 409 `window_closed` with `next_window` hint.

2. **Given** `cycle.state == 'VOTACION'` but `now() >= vote_until`,
   **When** either endpoint is called,
   **Then** HTTP 409 `window_closed`.

### User Story 4 — Sort options and cursor (Priority: P2)

Power users can sort by `recent` or `hot` if they prefer.

**Acceptance Scenarios**:

1. **Given** 30 approved twists,
   **When** the user calls `GET /vote-feed?sort=hot&limit=10`,
   **Then** HTTP 200 with the 10 highest-voted twists (tiebreak by `submitted_at
   ASC`), and `page.next_cursor` is a non-empty opaque string.

2. **Given** the same call followed by `?cursor=<value>&limit=10`,
   **When** the user advances,
   **Then** the next 10 twists are returned with no overlap.

3. **Given** `?sort=recent`,
   **When** called,
   **Then** items ordered by `submitted_at DESC`.

### User Story 5 — Voting on one's own twist (Priority: P2)

By default, self-voting is **allowed** (closed-beta family-friends context). The
PO can disable it via env config.

**Acceptance Scenarios**:

1. **Given** `ALLOW_SELF_VOTE=true` (default) and the user owns twist T (approved),
   **When** they POST `{twist_id: T}`,
   **Then** HTTP 200 with the vote persisted.

2. **Given** `ALLOW_SELF_VOTE=false`,
   **When** the user POSTs for their own twist,
   **Then** HTTP 409 `{"code":"cannot_self_vote"}`.

### Edge Cases

- **Voting on a non-approved twist** (`pending_review`, `rejected_*`,
  `deleted_by_user`): 409 `twist_not_votable`. The feed never returns these, so
  this should only happen if a client constructs the request from a stale cache.
- **Voting on a twist from a different chapter** (cross-chapter): 409
  `chapter_mismatch`. Defensive — `votes.chapter_id` is denormalized for fast
  quota counting.
- **Vote-feed during `FILTERING`**: 409 `window_closed`. The filter may still be
  in flight; serving partial data would be misleading.
- **Banned user**: 403 (handled by JWT middleware in 002).
- **Kill-switch active**: 503 (handled at the endpoint level).
- **Twist deleted by user between feed load and vote tap**: 409 `twist_not_votable`.
  The PWA gracefully removes the item.
- **Cursor with a different `sort` than originally**: 422 `cursor_invalid`. The
  cursor encodes the sort to detect cross-sort mismatches.
- **Feed query during a race**: votes are racing; `vote_count` may be slightly
  stale relative to other simultaneous votes. Eventual consistency is acceptable
  for closed beta.
- **Two devices same user**: the user has one device (module 002 OQ-AUTH-1).
  Quota and `has_my_vote` are user-scoped, so two-device users see consistent
  state.

## Requirements

### Functional Requirements

- **FR-001**: `GET /api/v1/twists/vote-feed` requires JWT. Returns approved twists
  for the **currently live chapter** (NOT past chapters).
- **FR-002**: Default sort: `random` with seed `sha256(cycle_id + user_id)[0:8]`
  interpreted as int. Optional: `recent`, `hot`. Cursor encodes
  `(sort, last_position, sort_value)` as base64-url JSON.
- **FR-003**: Page size: `limit` query param, default 25, max 100. Response includes
  `page.next_cursor` (null when no more) and `page.total_approved` (denormalized
  count of approved twists for the chapter).
- **FR-004**: Each item: `{id (uuid), content, vote_count, has_my_vote: bool}`. The
  `has_my_vote` field requires a JOIN against the votes table; the cost is bounded
  by the user's vote quota (≤ 5 rows).
- **FR-005**: `POST /api/v1/twists/vote` requires JWT. Body `{twist_id: UUID}`.
  Atomic semantics: `INSERT INTO votes (...) ON CONFLICT (twist_id, user_id) DO
  NOTHING`. If 0 rows affected → 409 `already_voted`.
- **FR-006**: Quota: `MAX_VOTES_PER_USER_PER_CHAPTER` (default 5, env-overridable).
  Race protection: `pg_advisory_xact_lock(hashtext('vote_quota:' || user_id || ':'
  || chapter_id))` before the count + insert.
- **FR-007**: Response on success: `{twist_id, new_vote_count, user_quota: {used,
  max, remaining}}`. `new_vote_count` is the post-insert count of votes for that
  twist.
- **FR-008**: Window enforcement: both endpoints validate `cycle.state ==
  'VOTACION'` AND `now() < vote_until`. Reuses module 004's window computation.
- **FR-009**: `ALLOW_SELF_VOTE` env flag (default `true`). When `false`, server
  rejects self-votes with 409 `cannot_self_vote`.
- **FR-010**: Idempotency: not required (UNIQUE constraint + `ON CONFLICT DO
  NOTHING` makes the operation naturally idempotent on `(twist_id, user_id)`).
  Optional `Idempotency-Key` honored for client convenience.
- **FR-011**: Kill-switch + banned + ChapterMismatch + ban handled identically to
  module 005.
- **FR-012**: Structured log `vote_cast {user_id, twist_id, chapter_id, outcome,
  new_vote_count}` on every vote attempt.
- **FR-013**: PWA flows:
  - New route `vote.svelte` rendered when `cycle_state == 'VOTACION'`.
  - Card-or-list view of the feed; tap "👍" to vote.
  - Optimistic vote_count increment; rollback on error.
  - Toast on `over_quota`, `already_voted`, `window_closed`.
  - "Mis votos" indicator (5 dots filled as user votes).

### Non-Functional Requirements

- **NFR-001**: `GET /vote-feed` p95 < 150 ms for 100 approved twists.
- **NFR-002**: `POST /vote` p95 < 200 ms (includes lock + insert).
- **NFR-003**: 20 concurrent voters with 5 votes each → 100 votes inserted, 0 5xx.
- **NFR-004**: 10 concurrent votes from same user for same twist → exactly 1
  inserted, 9 receive 409 `already_voted`. No 5xx, no deadlock.

### Out of Scope (for this feature)

- Downvotes / dislikes. The product is positive-curation.
- Vote weighting (e.g., reputation-based). Each vote = 1.
- Public vote-tally board ("who voted for what"). Privacy.
- Comments on twists. Out of MVP.
- Tag / search inside the feed. The feed is small enough that browsing works.
- Real-time vote updates (websocket / SSE). The PWA polls or refreshes manually.
