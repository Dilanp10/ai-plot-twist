# Task Breakdown: Daily Cycle FSM

**Branch**: `003-cycle-fsm` | **Date**: 2026-06-07

PR-sized chunks. Tasks marked `[P]` parallelize within their phase; `→ T-NNN` blocks.

---

## Phase 0 — Migrations (3 PRs)

### T-001 — `0004_seasons_chapters` → 001-merged
**Files**: `apps/api/alembic/versions/0004_seasons_chapters.py`
**Body**: as in [data-model.md §0004](./data-model.md#0004_seasons_chapterspy)
**Test**: `tests/integration/test_migrations.py::test_0004_upgrade_downgrade` twice.

### T-002 — `0005_cycles_transitions` → T-001
**Files**: `apps/api/alembic/versions/0005_cycles_transitions.py`
**Test**: assert UNIQUE index `uniq_st_trigger` exists; assert inserting same
`(cycle, to, trigger)` twice raises `IntegrityError`.

### T-003 — `0006_system_flags` → T-002 [P]
**Files**: `apps/api/alembic/versions/0006_system_flags.py`
**Test**: assert the `kill_switch` row exists with `on=false` after upgrade.

---

## Phase 1 — Pure domain (3 PRs)

### T-004 — `cycle_clock.py` (TZ helper) [P]
**Files**:
- `apps/api/app/domain/cycle_clock.py`
- `apps/api/tests/unit/test_cycle_clock.py`

**API**:
```python
@dataclass(frozen=True)
class ScheduleSlot:
    tick: Literal["ESTRENO","FILTERING","GENERACION","WATCHDOG"]
    art_local_time: str         # "12:00", "18:00", "23:00", "23:55"

def expected_state_at(when_utc: datetime) -> str: ...
def next_n_ticks(now_utc: datetime, n: int) -> list[ScheduleSlotInstance]: ...
def to_art(when_utc: datetime) -> datetime: ...
```

**Test**: feed UTC times around DST boundaries (even though ART has no DST, defend
against accidental introduction). Test "tomorrow at 12:00 ART" math.

### T-005 — `cycle_fsm.py` (pure) → 001-merged [P]
**Files**:
- `apps/api/app/domain/cycle_fsm.py`
- `apps/api/tests/unit/test_cycle_fsm.py`

**API**:
```python
@dataclass(frozen=True)
class TransitionPlan:
    to: str
    side_effect: str | None
    state_updates: dict        # e.g. {"next_chapter_id": None} or {"chapter_status": "live"}

def compute(
    current_state: str,
    requested_to: str,
    state_entered_at: datetime,
    now_utc: datetime,
    *, skip_dwell: bool = False,
) -> TransitionPlan: ...
```

Raises `IllegalTransition`, `TimeFenceViolation`.

**Test**: exhaustive — every (from, to) pair, 7×7 = 49 combinations, asserting legal/
illegal verdict matches [spec FR-004](./spec.md#functional-requirements). Property
test: `compute` is deterministic.

### T-006 — `side_effects.py` (DI registry + stubs) → T-005
**Files**:
- `apps/api/app/domain/side_effects.py`
- `apps/api/tests/unit/test_side_effects_stubs.py`

**API**:
```python
SideEffect = Callable[[int], Awaitable[None]]   # chapter_id → None
_registry: dict[str, SideEffect] = {}
def register(name: str, fn: SideEffect) -> None: ...
def get(name: str) -> SideEffect: ...
```

Ships stubs:
- `director_filter_stub(chapter_id)` → updates `twists.status='approved'` for all
  `pending_review` rows of `chapter_id`, then calls `cycle_executor.transition(...,
  to="VOTACION", triggered_by="side_effect", trigger_id=f"sefx-{uuid()}")`.
- `generation_pipeline_stub(chapter_id)` → INSERT new chapter cloning manifest,
  UPDATE cycle.next_chapter_id, then transition to PENDING_RELEASE.

---

## Phase 2 — Infra repos (5 PRs)

### T-007 — `SeasonsRepo` → T-001 [P]
**Files**: `apps/api/app/infra/seasons_repo.py` + integration test.
**Methods**: `insert(...)`, `get_active() -> Season | None`,
`mark_inactive(season_id)`.

### T-008 — `ChaptersRepo` → T-001 [P]
**Files**: `apps/api/app/infra/chapters_repo.py` + tests.
**Methods**: `insert(...)`, `get_by_id(id)`, `clone_manifest(src_id, next_day_index)`,
`mark_live(id)`, `list_by_season(season_id)`.

### T-009 — `CyclesRepo` → T-002 [P]
**Files**: `apps/api/app/infra/cycles_repo.py` + tests.
**Methods**: `insert(season_id, chapter_id, cycle_date)`, `get_active() -> Cycle`,
`update_state(id, new_state, next_chapter_id?)`, `lock_advisory(id)` (uses
`pg_advisory_xact_lock`).

### T-010 — `TransitionsRepo` → T-002 [P]
**Files**: `apps/api/app/infra/transitions_repo.py` + tests.
**Methods**: `insert(cycle_id, from, to, triggered_by, trigger_id, payload) ->
Transition | None` (returns None on UNIQUE conflict → "already applied"),
`get_recent(cycle_id, limit)`, `get_by_trigger(cycle_id, to_state, trigger_id)`.

### T-011 — `SystemFlagsRepo` → T-003 [P]
**Files**: `apps/api/app/infra/system_flags_repo.py` + tests + in-process 30 s LRU.

---

## Phase 3 — Executor (3 PRs)

### T-012 — `cycle_executor.transition` → T-004..T-011
**Files**:
- `apps/api/app/domain/cycle_executor.py`
- `apps/api/tests/integration/test_transition_happy.py`

**Behavior**: SQL outline from [data-model.md §Transition transaction](./data-model.md).

### T-013 — Side-effect safety wrapper → T-012
**Files**:
- `apps/api/app/domain/safe_side_effect.py`
- `apps/api/tests/integration/test_side_effect_crash.py`

**Behavior**: as in research R-008. On exception: force FAILED, set kill-switch,
Discord webhook, log.

### T-014 — Watchdog logic → T-004, T-012
**Files**:
- `apps/api/app/domain/watchdog.py`
- `apps/api/tests/integration/test_watchdog.py`

**Behavior**: schedule-aware stuck detection per research R-004 table.

---

## Phase 4 — HTTP layer (4 PRs)

### T-015 — Replace `/internal/transition` body → T-012, T-014
**Files**:
- `apps/api/app/api/internal_transition.py` (modified)
- `apps/api/tests/integration/test_transition_*.py` (all six failure modes)

**Notes**: the route handler now: validates HMAC (unchanged), parses payload, dispatches
to `WATCHDOG` handler (no mutation) or normal `cycle_executor.transition`. Maps
domain exceptions to RFC 7807 problem responses.

### T-016 — `admin_token` middleware → 001-merged [P]
**Files**: `apps/api/app/middleware/admin_token.py` + tests.

### T-017 — `POST /internal/kill-switch` → T-016, T-011
**Files**: `apps/api/app/api/internal_kill_switch.py` + tests.

### T-018 — `GET /internal/health/cycle` → T-004, T-009, T-010, T-011
**Files**: `apps/api/app/api/internal_health_cycle.py` + tests.

---

## Phase 5 — CLIs (3 PRs)

### T-019 — `pnpm bootstrap-cycle` → T-007, T-008, T-009
**Files**:
- `apps/api/app/scripts/bootstrap_cycle.py`
- `docs/seed/example-cap0.yaml`
- root + apps/api `package.json` delegation
- integration test.

### T-020 — `pnpm replay-tick` → T-015 [P]
**Files**:
- `apps/api/app/scripts/replay_tick.py`
- delegation in package.json.

Implements HMAC signing client-side, posts to local API. `--no-dwell-check` adds
header `X-Dev-Skip-Dwell: 1` which the executor honors **only** when `ENV != prod`.

### T-021 — `pnpm kill-switch` → T-017 [P]
**Files**: `apps/api/app/scripts/kill_switch.py` + delegation + test.

---

## Phase 6 — Enable cron workflows (1 PR)

### T-022 — Flip `tick-*.yml` from `workflow_dispatch` to `schedule:` → T-015
**Files**:
- `.github/workflows/tick-12-estreno.yml` (modified)
- `.github/workflows/tick-18-vote.yml` (modified)
- `.github/workflows/tick-23-generate.yml` (modified)
- `.github/workflows/tick-2355-watchdog.yml` (modified)

**Diff** per file: uncomment the `schedule:` block, add `retry_attempt` to the JSON
body (from `${{ github.run_attempt }}`), update curl to `--retry 3 --retry-delay 10`.

**Done when**: pushing the change to a branch shows the workflows as "scheduled" in
the GitHub UI, and a `workflow_dispatch` from the UI hits the live API with `to:
WATCHDOG`.

---

## Phase 7 — Constitution amendment + ADR (1 PR)

### T-023 — Document Spanish FSM state names → T-005
**Files**:
- `docs/adr/0001-spanish-fsm-state-names.md`
- `.specify/memory/constitution.md` (Gate 6 footnote)

**Body**: short ADR linking to research R-009.

---

## Phase 8 — Chaos + e2e (2 PRs)

### T-024 — Concurrent-tick chaos test → T-015
**Files**: `apps/api/tests/integration/test_chaos_concurrent.py`

**Behavior**: spawn 50 asyncio tasks, all POSTing the same `(to, trigger_id)` to a
real running API. Assert exactly one `state_transitions` row, all responses are 200
or 202, no 5xx.

### T-025 — Day-in-a-minute e2e → all prior
**Files**: `apps/api/tests/integration/test_day_in_a_minute.py`

**Behavior**: bootstrap, then force-fire ESTRENO → RECEPCION_IDEAS → FILTERING →
VOTACION → GENERACION → PENDING_RELEASE → ESTRENO (day 2). Asserts all state
transitions, all stub side-effects, chapter day_index increments, no FAILED state.

---

## Phase 9 — Deploy + observe (1 PR)

### T-026 — Live observe one calendar day → T-022, T-025
**Files**:
- `specs/003-cycle-fsm/quickstart.md` (verified post-deploy)
- `specs/README.md` (mark module `done`)

**Done when**: a 24 h window on Fly shows 4 `state_transitions` rows generated by the
cron with no manual intervention; `health/cycle` reports a fresh `PENDING_RELEASE`
at 23:30.

---

## Done-when (module-level acceptance)

1. All 26 tasks merged.
2. Every box in [checklists/requirements.md](./checklists/requirements.md) ticked.
3. A live deploy completes one full cycle unattended (with stubs).
4. `specs/README.md` updated.

---

## Estimates (solo dev, calendar days)

| Phase | Tasks | Est. days |
|---|---|---|
| 0 — Migrations | T-001..T-003 | 1 |
| 1 — Pure domain | T-004..T-006 | 2 |
| 2 — Infra repos | T-007..T-011 | 2 |
| 3 — Executor | T-012..T-014 | 2.5 |
| 4 — HTTP layer | T-015..T-018 | 2 |
| 5 — CLIs | T-019..T-021 | 1.5 |
| 6 — Cron flip | T-022 | 0.5 |
| 7 — ADR | T-023 | 0.5 |
| 8 — Chaos + e2e | T-024..T-025 | 1.5 |
| 9 — Live observe | T-026 | 1 (calendar; ≈ 0.25 working) |
| **Total** | 26 tasks | **≈ 14.75 days** |

Buffer +30% for advisory-lock + chaos-test surprises → **plan for 19 working days**.
