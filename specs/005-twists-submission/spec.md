# Feature Specification: Twist Submission, Deletion, and Listing

**Feature Branch**: `005-twists-submission`
**Created**: 2026-06-07
**Status**: Draft
**Depends on**: `001-project-bootstrap`, `002-auth-invite-flow`, `003-cycle-fsm`

## Summary

The first authenticated mutating surface of the product. Authenticated users can
**submit** continuation proposals ("twists") for the current live chapter during the
`RECEPCION_IDEAS` window, **soft-delete** their own pending twists before the filter
runs, and **list** their twists for the current chapter (with status visible so they
can see rejections after module 006 ships).

Hard rules: 5–280 chars, NFKC-normalized, max 3 per user per chapter (configurable),
window-gated by `cycle.state == 'RECEPCION_IDEAS'` AND `now() < submit_until`, race-
protected by a per-user-per-chapter advisory lock, idempotent on `Idempotency-Key`.
This module does NOT run the LLM filter — twists land with `status='pending_review'`
and stay there until module 006 transitions them.

## User Scenarios & Testing

### User Story 1 — User submits a twist during the window (Priority: P1)

A logged-in user reads the day's chapter at 14:00 ART, writes a continuation idea,
hits "Tirá la idea". The server stores it and returns the remaining quota.

**Why this priority**: this is the core contribution mechanism. Without it, there is
no community input — no plot twists, no game.

**Independent Test**: bootstrap, force `RECEPCION_IDEAS`, redeem an invite, POST a
valid twist. Verify HTTP 201, row in `twists` with `status='pending_review'`, response
contains the twist with `public_id`.

**Acceptance Scenarios**:

1. **Given** the cycle is in `RECEPCION_IDEAS`, the user is authenticated, and they
   have 0 twists for the current chapter,
   **When** they POST `{chapter_id, content: "…valid…"}` to `/api/v1/twists/submit`,
   **Then** HTTP 201 with body `{twist: {public_id, chapter_id, content, status:
   "pending_review", submitted_at}, remaining_submissions: 2}`. The DB shows one
   `twists` row.

2. **Given** the user has Idempotency-Key `K`,
   **When** they POST the same body twice with the same `K`,
   **Then** the first call inserts and returns 201; the second call returns 200 with
   the same body. Only one row exists.

3. **Given** a different body with the same key `K`,
   **When** they POST the second call,
   **Then** HTTP 409 `{"code":"idempotency_conflict"}`.

### User Story 2 — Submission outside the window is rejected (Priority: P1)

A user tries to submit at 18:00:30 (30 s after the cron flipped the cycle to
`FILTERING`).

**Acceptance Scenarios**:

1. **Given** `cycle.state != 'RECEPCION_IDEAS'`,
   **When** the user POSTs `/twists/submit`,
   **Then** HTTP 409 `{"code":"window_closed", "next_window": {"opens_at": "<iso>"}}`.
   No DB write.

2. **Given** `cycle.state == 'RECEPCION_IDEAS'` but `now() ≥ submit_until`
   (clock skew edge),
   **When** the user POSTs,
   **Then** HTTP 409 `{"code":"window_closed"}`. The user-facing message: "La ventana
   de propuestas cerró a las 18:00 ART."

### User Story 3 — Quota enforcement is race-safe (Priority: P1)

A user has 2 twists. They open two tabs and submit a 3rd and 4th simultaneously
(both pass the naive `count < 3` check).

**Why this priority**: quota leaks would let one user dominate the feed.

**Acceptance Scenarios**:

1. **Given** the user has 2 active twists,
   **When** they fire two concurrent submits within 50 ms,
   **Then** exactly one succeeds (HTTP 201) and the other gets HTTP 409
   `{"code":"over_quota","quota_used":3,"quota_max":3}`. DB count = 3.

2. **Given** the user has 3 twists (any combination of statuses including
   `deleted_by_user`),
   **When** they POST a 4th,
   **Then** HTTP 409 `over_quota`. Deleted twists DO count toward the quota; this is
   the anti-spam-then-delete-loop guard.

### User Story 4 — User soft-deletes a pending twist (Priority: P2)

After hitting submit, the user notices a typo. They click "Borrar" on the twist
inside their `/me/twists` panel.

**Acceptance Scenarios**:

1. **Given** the user owns twist `T` with `status='pending_review'`, the cycle is in
   `RECEPCION_IDEAS`, and `now() < submit_until`,
   **When** they DELETE `/api/v1/twists/{T.public_id}`,
   **Then** HTTP 200 with `{twist_id, deleted_at, remaining_submissions: <unchanged>}`.
   DB row remains with `status='deleted_by_user'`, `deleted_at=now()`.

2. **Given** the twist is owned by another user,
   **When** the user DELETEs it,
   **Then** HTTP 403 `forbidden_not_owner`. (Use 403 not 404 — the user knows the id
   came from their UI.)

3. **Given** the cycle has advanced to `FILTERING` (filter already ran),
   **When** the user DELETEs a previously-pending twist,
   **Then** HTTP 409 `already_filtered`. The twist is now in
   `approved/rejected_*` and is immutable.

4. **Given** the twist is already `deleted_by_user`,
   **When** the user re-DELETEs it,
   **Then** HTTP 200 (idempotent) with the original `deleted_at`.

### User Story 5 — User lists own twists for current chapter (Priority: P2)

The PWA shows a "Mis ideas" panel with each twist + its current status, so the user
sees their pending vs approved vs rejected verdicts after the 18:00 filter runs.

**Acceptance Scenarios**:

1. **Given** the user has 3 twists across various statuses,
   **When** they GET `/api/v1/me/twists`,
   **Then** HTTP 200 with `{items: [twist, twist, twist], quota: {used: 3, max: 3,
   remaining: 0}}`. Items include status and `director_reason` (the brief LLM
   justification from module 006) when status is `rejected_*`.

2. **Given** the user has 0 twists,
   **When** they GET `/me/twists`,
   **Then** HTTP 200 with `{items: [], quota: {used: 0, max: 3, remaining: 3}}`.

### Edge Cases

- **Content all whitespace / zero-width chars**: NFKC + control-strip + trim, then
  length check. "   " → 0 chars after trim → 422.
- **Content with display-name-like patterns** ("@Lucía"): no special handling in this
  module; module 006 filter decides if it's spammy.
- **Submitting for the wrong chapter id** (e.g., yesterday's `public_id`): 409
  `chapter_mismatch` — twists target only the currently live chapter.
- **Submitting while banned**: the JWT middleware already returns 403 `banned`.
- **Kill-switch active**: 503 `under_maintenance`. Consistent with module 004.
- **Cycle is in `FAILED` state**: 503 `under_maintenance` (the PO is expected to set
  kill-switch on).
- **Idempotency-Key reuse after 14 days**: the key has expired from
  `idempotency_keys`; treated as a new request. Document for client retry guidance.
- **DELETE with no twist content change after submit**: explicitly allowed, distinct
  from "edit" (which is forbidden — there is no PATCH endpoint).
- **Pagination on `/me/twists`**: not required in MVP; quota cap of 3 makes the list
  trivially small.

## Requirements

### Functional Requirements

- **FR-001**: `POST /api/v1/twists/submit` requires JWT (uses module 002 middleware).
  Body: `{chapter_id: UUID, content: str}`. Header: `Idempotency-Key: UUID`
  (required to harden against double-tap).
- **FR-002**: Validation order: JWT → kill-switch check → request-shape (422) →
  chapter lookup (404 if missing, 409 `chapter_mismatch` if not the active chapter)
  → state gate (`RECEPCION_IDEAS` AND `now() < submit_until`) → content
  normalization → quota check (under advisory lock) → INSERT.
- **FR-003**: Content normalization: `nfkc(content).strip()` then strip control chars
  in the Unicode `Cc` category, then validate `5 ≤ len ≤ MAX_TWIST_LEN` (default 280).
- **FR-004**: Quota: `MAX_TWISTS_PER_USER_PER_CHAPTER` (default 3, env-overridable).
  The count is `COUNT(*) FROM twists WHERE user_id=? AND chapter_id=?` — **all
  statuses including `deleted_by_user`**, to prevent spam-then-delete cycles.
- **FR-005**: Race protection: the submit transaction acquires
  `pg_advisory_xact_lock(hashtext('twist_quota:' || user_id || ':' || chapter_id))`
  before the count check. Lock timeout 1 s; on timeout, 503 `lock_busy`.
- **FR-006**: New twists land with `status='pending_review'`. The director's filter
  (module 006) transitions them later; this module never sets approved/rejected.
- **FR-007**: `DELETE /api/v1/twists/{public_id}` requires JWT, validates ownership
  (`twists.user_id == jwt.user_id`), validates `cycle.state ==
  'RECEPCION_IDEAS' AND now() < submit_until`, validates `twists.status ==
  'pending_review' OR 'deleted_by_user'` (idempotent). Sets `status='deleted_by_user'`
  + `deleted_at=now()`. Does NOT free quota (FR-004).
- **FR-008**: `GET /api/v1/me/twists` requires JWT. Returns up to N twists for the
  currently live chapter (N = quota max). Each item:
  `{public_id, content, status, director_reason?, submitted_at, deleted_at?}`.
  Response also includes `quota: {used, max, remaining}`.
- **FR-009**: Response for `submit` and `delete` MUST include `remaining_submissions`
  computed as `max(0, MAX_TWISTS_PER_USER_PER_CHAPTER - quota_used)`. After delete,
  this value does NOT increase (FR-004).
- **FR-010**: Idempotency: `submit` honors `Idempotency-Key`. Same key + same hash of
  body within 14 days → return cached response (200, not 201). Same key + different
  hash → 409 `idempotency_conflict`. DELETE is naturally idempotent; no
  `Idempotency-Key` required.
- **FR-011**: Structured logging: `twist_submitted {user_id, chapter_id, twist_id,
  outcome}` and `twist_deleted {user_id, chapter_id, twist_id, outcome}` emitted
  with redacted `content` (first 20 chars + `…` if longer). Full content lives in DB
  only.
- **FR-012**: PWA flows:
  - Add a "Tirá una idea" CTA on `today.svelte` that renders only when
    `cycle_state == 'RECEPCION_IDEAS'`.
  - Modal with a `<textarea maxlength="280">` and a counter.
  - On submit, optimistic UI: append to "Mis ideas" list with `status="pending_review"`.
  - "Mis ideas" panel with delete button (visible only while submit window is open).

### Non-Functional Requirements

- **NFR-001**: `POST /twists/submit` p95 < 250 ms (includes lock + INSERT).
- **NFR-002**: `DELETE /twists/{id}` p95 < 150 ms.
- **NFR-003**: `GET /me/twists` p95 < 100 ms.
- **NFR-004**: 100 concurrent submits across distinct users sustained for 10 s with
  0 5xx; advisory lock contention only within same-user-same-chapter.
- **NFR-005**: 10 concurrent submits for the SAME (user, chapter) → exactly
  `min(10, MAX)` succeed; the rest are clean 409 `over_quota`. No 5xx, no deadlock.

### Out of Scope (for this feature)

- LLM content filter — module 006.
- Editing a twist — forbidden by design (gaming prevention). [closed]
- Voting on twists — module 007.
- Public listing of others' twists in `RECEPCION_IDEAS` — privacy decision, deferred.
- Notifying the user when their twist is rejected — push system, module 011.
- Reporting / flagging other users' twists — out of MVP scope.
