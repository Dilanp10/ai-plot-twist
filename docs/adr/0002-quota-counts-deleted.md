# ADR-0002: Twist quota counts deleted rows

**Date**: 2026-06-12
**Status**: Accepted
**Module**: 005 — twists-submission
**Related**: SDD §5.5, [specs/005-twists-submission/research.md](../../specs/005-twists-submission/research.md) R-003

## Context

The SDD originally declared two contradictory things about the twist quota:

> "Borrar no libera quota" (prose, multiple places)

versus the formula written in §5.5:

> "MAX − count(twists WHERE status IN ('pending_review','approved','rejected_*'))"

The formula **excludes** `deleted_by_user` from the count, which means a
delete DOES free a slot — contradicting the prose. Discovered while
designing module 005 (research round 3).

## Decision

**The prose is correct. The formula is wrong.**

Module 005 implements `quota_used = COUNT(*) FROM twists WHERE user_id=? AND
chapter_id=?` over **all statuses including `deleted_by_user`**.

The SDD §5.5 §"Nota" has been updated to match (see commit `83701b5`'s
predecessor branch). The implementation lives in:

- [`TwistsRepo.count_for_user_chapter`](../../apps/api/app/infra/twists_repo.py)
- [`TwistSubmissionService.submit`](../../apps/api/app/domain/twist_submission.py)
  (recount-under-lock + `OverQuota` raise)
- 2 dedicated integration tests:
  [`test_twist_submit_quota.py::test_deleted_twists_count_toward_quota`](../../apps/api/tests/integration/test_twist_submit_quota.py)
  and [`test_twists_repo.py::test_count_includes_deleted_status`](../../apps/api/tests/integration/test_twists_repo.py).

## Rationale

The explicit anti-pattern the PO and the spec want to prevent is
**spam-then-delete cycling**: a user submits 3 twists, deletes 1 to get
"a slot back", submits a 4th, deletes 1, and so on — turning a 3-slot
quota into an unlimited spammer pipe.

Counting deletes toward the quota makes a delete operationally equivalent
to a final twist: the user "spent" a quota slot, regardless of whether
the row ends up in `pending_review` or `deleted_by_user`. This is what
"borrar no libera quota" means in operational terms.

## Consequences

- The HTTP `DELETE /twists/{public_id}` response includes
  `remaining_submissions` that is **unchanged** from before the delete.
  Module 005 documents this explicitly in the contract.
- The PWA's optimistic-delete flow (`twistStore.remove`) syncs the
  server-side `remaining_submissions` rather than locally incrementing.
- Module 007 (voting) and module 006 (filter) inherit the same
  semantic: they query `twists` by status as appropriate but never
  assume the count of one status equals "used quota".

## Alternatives considered

1. **Honor the original SDD formula** (delete frees quota). Rejected
   because it enables the spam-then-delete loop the PO explicitly
   wanted to prevent.
2. **Add a hard cap on delete operations per chapter**. Rejected as a
   weaker form of the same protection that adds new edge cases (what
   counts as a delete? what about retries?).
