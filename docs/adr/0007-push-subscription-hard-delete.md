# ADR-0007 — Hard Delete for Push Subscriptions (Gate 7 Carve-out)

**Status**: Accepted  
**Date**: 2026-06-15  
**Deciders**: Dilan Perea (PO + lead dev)  
**Links**: Module 011 T-010, Gate 7 (`.specify/memory/constitution.md`)

---

## Context

Gate 7 of the project constitution requires all user-authored entities to use
soft delete (flag/status field) rather than `DELETE FROM`, preserving an audit
trail and enabling accidental-delete recovery.

The `push_subscriptions` table stores Web Push endpoint credentials for each
user device. When a user unsubscribes (or a push service returns 410 Gone),
the row must be removed.

## Decision

Push subscriptions are **hard-deleted** (`DELETE FROM push_subscriptions`).
This is an explicit carve-out from Gate 7.

## Rationale

Push subscriptions are **technical infrastructure, not user-authored content**:

1. **No business value in retention**: unlike twists or votes, a revoked
   push subscription has zero audit or recovery value. The endpoint credential
   is invalidated by the browser or push service; retaining a dead row only
   wastes space and causes silent fan-out failures.

2. **Security**: endpoint URLs + encryption keys are PII-adjacent. Keeping
   revoked credentials longer than necessary increases the attack surface.
   Hard delete minimises data retention.

3. **Operational correctness**: the 410 Gone path (push service tells us the
   endpoint is permanently revoked) requires removal so the next fan-out does
   not retry a dead endpoint. A soft-delete would require every fan-out to
   filter by status — adding complexity with no benefit.

4. **Re-subscribe resets the row**: if a user re-subscribes from the same
   browser, `PushSubscriptionsRepo.upsert` reuses the existing row via
   `ON CONFLICT (endpoint) DO UPDATE`. Hard delete of the old row is clean;
   soft delete would require upsert + status reset logic.

## Boundary

This carve-out covers **only** `push_subscriptions`. Twists and votes remain
under Gate 7 (soft delete / `deleted_by_user` flag). Any future technical
infrastructure table (session tokens, device registrations) that meets the
same criteria must go through a separate ADR rather than inheriting this one.

## Consequences

- Gate 7 checklist in the constitution carries a footnote pointing here.
- `PushSubscriptionsRepo.delete_by_id_for_user` issues `DELETE FROM`; no
  `deleted_at` or `status` column exists on the table.
- The 410 Gone bulk-delete path in `push_fanout` and `post_internal_push_test`
  both issue hard deletes without the Gate 7 concern applying.
