# Task Breakdown: Voting

**Branch**: `007-voting` | **Date**: 2026-06-07

---

## Phase 0 — Migration (1 PR)

### T-001 — `0008_votes` → 003-merged
**Files**:
- `apps/api/alembic/versions/0008_votes.py`
- `apps/api/tests/integration/test_migrations.py::test_0008_upgrade_downgrade`

---

## Phase 1 — Pure domain (2 PRs, parallel)

### T-002 — `vote_sort.py` [P]
**Files**:
- `apps/api/app/domain/vote_sort.py`
- `apps/api/tests/unit/test_vote_sort.py`

**API**:
```python
def seed_int(cycle_id: int, user_id: int) -> int: ...   # sha256 → int
def shuffle_stable(items: list, *, cycle_id: int, user_id: int) -> list: ...
def sort_recent(items: list) -> list: ...    # by submitted_at DESC
def sort_hot(items: list) -> list: ...       # by vote_count DESC, submitted_at ASC
```

**Tests**: same seed → same order (100 trials); different seeds → different orders
(statistical); tiebreak rules.

### T-003 — `vote_cursor.py` [P]
**Files**:
- `apps/api/app/domain/vote_cursor.py`
- `apps/api/tests/unit/test_vote_cursor.py`

**API**:
```python
@dataclass(frozen=True)
class Cursor:
    sort: Literal["random","recent","hot"]
    last_position: int
    last_sort_value: int | str | None

def encode(c: Cursor) -> str: ...    # base64-url(json)
def decode(s: str) -> Cursor: ...    # raises CursorInvalid
```

---

## Phase 2 — Infra repo (1 PR)

### T-004 — `VotesRepo` → T-001
**Files**:
- `apps/api/app/infra/votes_repo.py`
- `apps/api/tests/integration/test_votes_repo.py`

**Methods**:
- `count_for_user_chapter(user_id, chapter_id) -> int`
- `list_for_user_chapter(user_id, chapter_id) -> list[Vote]`
- `vote_atomic(twist_id, user_id, chapter_id) -> int | None` (returns the new
  row id, or `None` if ON CONFLICT)
- `count_for_twist(twist_id) -> int`
- `lock_user_chapter(user_id, chapter_id) -> None` (advisory)

---

## Phase 3 — Service (1 PR)

### T-005 — `VoteService` → T-002..T-004
**Files**:
- `apps/api/app/domain/vote_service.py`
- `apps/api/tests/integration/test_vote_cast_happy.py`
- `apps/api/tests/integration/test_vote_cast_double.py`
- `apps/api/tests/integration/test_vote_cast_over_quota.py`
- `apps/api/tests/integration/test_vote_cast_self_vote.py`
- `apps/api/tests/integration/test_vote_window_closed.py`

**API**:
```python
class VoteService:
    async def feed(self, user_id, sort, limit, cursor) -> FeedDTO: ...
    async def cast(self, user_id, twist_public_id) -> VoteDTO: ...
```

**Raises**: `WindowClosed`, `OverQuota`, `AlreadyVoted`, `TwistNotVotable`,
`CannotSelfVote`, `LockBusy`, `KillSwitchActive`.

---

## Phase 4 — HTTP endpoints (2 PRs)

### T-006 — `GET /twists/vote-feed` → T-005
**Files**:
- `apps/api/app/api/voting.py`
- `apps/api/tests/integration/test_vote_feed.py`
- `apps/api/tests/integration/test_vote_feed_cursor.py`

### T-007 — `POST /twists/vote` → T-005 [P]
**Files**:
- `apps/api/app/api/voting.py` (extend)
- `apps/api/tests/integration/test_vote_cast_endpoint.py`

---

## Phase 5 — Race tests (1 PR)

### T-008 — Concurrent vote tests → T-007
**Files**:
- `apps/api/tests/integration/test_vote_race_same_twist.py`
- `apps/api/tests/integration/test_vote_race_quota_edge.py`

**Behavior**:
- Same-twist race: 10 concurrent votes from same user → exactly 1 inserted.
- Quota-edge race: user with 4 votes fires 2 concurrent on different twists →
  exactly 1 succeeds.

CI runs each 50 times.

---

## Phase 6 — PWA (4 PRs)

### T-009 — `vote-api.ts` [P]
**Files**:
- `apps/web/src/lib/vote-api.ts`
- `apps/web/tests/vote-api.test.ts`

### T-010 — `vote-store.ts` (Svelte 5 runes) → T-009
**Files**:
- `apps/web/src/lib/vote-store.ts`
- `apps/web/tests/vote-store.test.ts`

**API**:
```ts
export const voteStore = {
  items: $state<FeedItem[]>([]),
  quota: $state<Quota>({used:0, max:5, remaining:5}),
  page: $state<Page>({...}),
  load(opts?: {sort, cursor}): Promise<void>,
  cast(twistId: string): Promise<void>,   // optimistic
};
```

### T-011 — `VoteCard.svelte` + `MyVotesIndicator.svelte` → T-010 [P]
**Files**:
- `apps/web/src/lib/components/VoteCard.svelte`
- `apps/web/src/lib/components/MyVotesIndicator.svelte`
- `apps/web/tests/vote-card.test.ts`

### T-012 — `vote.svelte` route → T-011
**Files**:
- `apps/web/src/routes/vote.svelte`
- `apps/web/src/App.svelte` (route mapping; auto-pick `/vote` when state=VOTACION)
- `apps/web/tests/vote-route.test.ts`

---

## Phase 7 — Deploy + observe (1 PR)

### T-013 — Deploy + smoke → T-006..T-012
**Files**:
- `specs/007-voting/quickstart.md` (verified)
- `specs/README.md` (mark 007 done; 008 in-progress)

**Done when**: real users cast votes during a real `VOTACION` window; PWA renders
correctly; race tests green.

---

## Done-when (module-level acceptance)

1. All 13 tasks merged.
2. Every box in [checklists/requirements.md](./checklists/requirements.md) ticked.
3. The `votes` table is populated by real beta-cohort activity and ready for
   module 008's winner-selection query.

---

## Estimates (solo dev, calendar days)

| Phase | Tasks | Est. days |
|---|---|---|
| 0 — Migration | T-001 | 0.5 |
| 1 — Pure domain | T-002..T-003 | 1 |
| 2 — Infra | T-004 | 1 |
| 3 — Service | T-005 | 1.5 |
| 4 — Endpoints | T-006..T-007 | 1 |
| 5 — Race | T-008 | 0.5 |
| 6 — PWA | T-009..T-012 | 2.5 |
| 7 — Deploy | T-013 | 0.5 |
| **Total** | 13 tasks | **≈ 8.5 days** |

Buffer +20% → **plan for 10 working days**.
