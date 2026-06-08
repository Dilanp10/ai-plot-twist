# Task Breakdown: Twist Submission

**Branch**: `005-twists-submission` | **Date**: 2026-06-07

PR-sized chunks.

---

## Phase 0 — Migration (1 PR)

### T-001 — `0007_twists` → 003-merged
**Files**:
- `apps/api/alembic/versions/0007_twists.py`
- `apps/api/tests/integration/test_migrations.py::test_0007_upgrade_downgrade`

**Body**: as in [data-model.md §0007](./data-model.md#0007_twistspy).

---

## Phase 1 — Pure domain (2 PRs, parallel)

### T-002 — `twist_content.py` [P]
**Files**:
- `apps/api/app/domain/twist_content.py`
- `apps/api/tests/unit/test_twist_content.py`

**API**:
```python
MIN_LEN = 5
MAX_LEN = 280
def normalize(raw: str) -> str: ...  # NFKC + Cc-strip + trim; raises ValueError if oob
```

**Tests**: RTL overrides, zero-width, emojis preserved, control chars stripped,
whitespace-only rejected, boundary lengths.

### T-003 — `twist_quota.py` [P]
**Files**:
- `apps/api/app/domain/twist_quota.py`
- `apps/api/tests/unit/test_twist_quota.py`

**API**:
```python
@dataclass(frozen=True)
class QuotaState:
    used: int
    max: int
    @property
    def remaining(self) -> int: ...
    @property
    def at_capacity(self) -> bool: ...
```

---

## Phase 2 — Infra repo (1 PR)

### T-004 — `TwistsRepo` → T-001
**Files**:
- `apps/api/app/infra/twists_repo.py`
- `apps/api/tests/integration/test_twists_repo.py`

**Methods**:
- `count_for_user_chapter(user_id, chapter_id) -> int` (all statuses)
- `insert(chapter_id, user_id, content) -> Twist`
- `get_by_public_id_for_update(public_id) -> Twist | None`
- `soft_delete(twist_id) -> datetime`
- `list_for_user_chapter(user_id, chapter_id, limit) -> list[Twist]`
- `lock_user_chapter(user_id, chapter_id) -> None`  (advisory lock helper)

---

## Phase 3 — Service (2 PRs)

### T-005 — `TwistSubmissionService.submit` → T-002, T-003, T-004
**Files**:
- `apps/api/app/domain/twist_submission.py`
- `apps/api/tests/integration/test_twist_submit_happy.py`
- `apps/api/tests/integration/test_twist_submit_idempotency.py`
- `apps/api/tests/integration/test_twist_submit_quota.py`

**Behavior**: SQL outline from
[data-model.md §Submission transaction](./data-model.md). Calls module 004's
`content_service.get_active_cycle_and_chapter()` for state and chapter resolution.

**Raises** typed domain errors: `WindowClosed`, `OverQuota`, `ChapterMismatch`,
`IdempotencyConflict`, `KillSwitchActive`, `LockBusy`.

### T-006 — `TwistSubmissionService.delete` and `.list_mine` → T-004
**Files**:
- `apps/api/app/domain/twist_submission.py` (extend)
- `apps/api/tests/integration/test_twist_delete.py`
- `apps/api/tests/integration/test_twist_delete_after_filter.py`
- `apps/api/tests/integration/test_me_twists.py`

**Behavior**: as in [data-model.md §Delete transaction](./data-model.md).

---

## Phase 4 — HTTP endpoints (3 PRs)

### T-007 — `POST /twists/submit` → T-005
**Files**:
- `apps/api/app/api/twists.py`
- `apps/api/tests/integration/test_twists_submit_endpoint.py`

**Behavior**: Pydantic body model, Idempotency-Key header parsing, JWT dependency
(from 002), kill-switch check (from 004 helper), map domain errors → RFC 7807.

### T-008 — `DELETE /twists/{public_id}` → T-006, T-007 [P]
**Files**:
- `apps/api/app/api/twists.py` (extend)
- `apps/api/tests/integration/test_twists_delete_endpoint.py`

### T-009 — `GET /me/twists` → T-006 [P]
**Files**:
- `apps/api/app/api/me_twists.py`
- `apps/api/tests/integration/test_me_twists_endpoint.py`

---

## Phase 5 — Race + chaos (1 PR)

### T-010 — Race test → T-007
**Files**:
- `apps/api/tests/integration/test_twist_submit_race.py`

**Behavior**: as in research R-009. CI runs 50 iterations.

---

## Phase 6 — PWA (4 PRs)

### T-011 — `twist-api.ts` typed client wrappers → 002-merged
**Files**:
- `apps/web/src/lib/twist-api.ts`
- `apps/web/tests/twist-api.test.ts`

**API**: `submitTwist(chapterId, content) → SubmitResponse`,
`deleteTwist(publicId) → DeleteResponse`, `getMyTwists() → MeTwistsResponse`.
Generates `Idempotency-Key` per attempt; reuses on retry.

### T-012 — `twist-store.ts` (Svelte 5 runes) → T-011
**Files**:
- `apps/web/src/lib/twist-store.ts`
- `apps/web/tests/twist-store.test.ts`

**API**:
```ts
export const twistStore = {
  mine: $state<TwistMine[]>([]),
  quota: $state<Quota>({used:0, max:3, remaining:3}),
  load(): Promise<void>,
  submit(content: string): Promise<void>,   // optimistic
  remove(publicId: string): Promise<void>,  // optimistic
};
```

### T-013 — `TwistModal.svelte` + `MyTwistsPanel.svelte` → T-012 [P]
**Files**:
- `apps/web/src/lib/components/TwistModal.svelte`
- `apps/web/src/lib/components/MyTwistsPanel.svelte`
- `apps/web/tests/twist-modal.test.ts`
- `apps/web/tests/my-twists-panel.test.ts`

### T-014 — Integrate into `today.svelte` → T-013, module 004's `today.svelte`
**Files**:
- `apps/web/src/routes/today.svelte` (extend)

**Behavior**: CTA visible iff `chapterStore.data.cycle_state === 'RECEPCION_IDEAS'`.
Panel collapsible. Toast notifications wired.

---

## Phase 7 — SDD patch + deploy (2 PRs)

### T-015 — Apply SDD §5.5 patch → T-005
**Files**:
- `SDD.md` (modify §5.5 quota formula per research R-003)
- `docs/adr/0002-quota-counts-deleted.md` (short ADR linking research R-003)

### T-016 — Deploy + smoke → T-007..T-014
**Files**:
- `specs/005-twists-submission/quickstart.md` (verified)
- `specs/README.md` (mark 005 done; 006 in-progress)

---

## Done-when (module-level acceptance)

1. All 16 tasks merged.
2. Every box in [checklists/requirements.md](./checklists/requirements.md) ticked.
3. SDD patch applied.
4. A real user can submit, delete, and list in the deployed PWA.

---

## Estimates (solo dev, calendar days)

| Phase | Tasks | Est. days |
|---|---|---|
| 0 — Migration | T-001 | 0.5 |
| 1 — Pure domain | T-002..T-003 | 1 |
| 2 — Infra | T-004 | 1 |
| 3 — Service | T-005..T-006 | 2 |
| 4 — Endpoints | T-007..T-009 | 1.5 |
| 5 — Race | T-010 | 0.5 |
| 6 — PWA | T-011..T-014 | 2.5 |
| 7 — SDD patch + deploy | T-015..T-016 | 1 |
| **Total** | 16 tasks | **≈ 10 days** |

Buffer +20% → **plan for 12 working days**.
