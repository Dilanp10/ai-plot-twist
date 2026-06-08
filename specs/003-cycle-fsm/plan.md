# Implementation Plan: Daily Cycle FSM

**Branch**: `003-cycle-fsm` | **Date**: 2026-06-07 | **Spec**: [spec.md](./spec.md)
**Depends on**: `001-project-bootstrap`

## Summary

Promote `POST /internal/transition` from HMAC stub to the real state engine. Add four
tables (`seasons`, `chapters`, `cycles`, `state_transitions`) and one auxiliary
(`system_flags` for kill-switch). Enable the four scheduled tick workflows. Provide
two admin endpoints (`kill-switch`, `health/cycle`) and three CLIs (`bootstrap-cycle`,
`replay-tick`, `kill-switch`). Ship stub side-effects so the loop runs end-to-end
before modules 006/008 land.

The architecture splits in three:

1. **Pure FSM** (`app/domain/cycle_fsm.py`): `compute_next_state(current_state,
   trigger, now_utc) -> Plan` — exhaustively unit-tested, zero I/O.
2. **State executor** (`app/domain/cycle_executor.py`): acquires the advisory lock,
   inserts `state_transitions`, mutates `cycles`, schedules background side-effects.
3. **Side-effect DI registry** (`app/domain/side_effects.py`): two callables
   (`director_filter`, `generation_pipeline`) resolved at startup. Module 003 ships
   stubs; later modules override.

## Technical Context

**Languages/Versions**: same as 001/002.
**New API dependencies**: none required; `apscheduler` evaluated and **rejected**
(see research R-002). Background side-effects use FastAPI's built-in
`BackgroundTasks` for fire-and-forget + `asyncio.create_task` for richer lifecycle
when needed. `python-ulid` already in deps from 002.
**Storage**: 5 new tables. Largest schema migration of the project so far.
**Testing**: integration tests use `freezegun` for time-fence assertions.
**Project type**: same.
**Performance Goals**: see NFR-001..NFR-004 in spec.
**Constraints**: no Redis, no external job queue. Background tasks run in-process; if
the Fly machine restarts mid-task, the watchdog detects and recovers.
**Scale/Scope**: one active cycle at any time; ≤ 1 transition per 5 min in steady state.

## Constitution Check

### Gate 1 — Zero-Cost
- [x] No new paid services. Discord webhook = free; HMAC unchanged.

### Gate 2 — Idempotency
- [x] **Critical for this module.** `UNIQUE(cycle_id, to_state, trigger_id)` +
      advisory lock + 200 `already_applied` branch. Tested in
      `test_transition_idempotency.py` with 50 concurrent ticks.
- [x] Bootstrap is idempotent on `(season.slug, day_index)` UNIQUE.

### Gate 3 — TZ Anchoring
- [x] All schedules computed in `America/Argentina/Buenos_Aires`.
      `cycle_date::DATE` evaluated in TZ-aware fashion.
- [x] Cron expressions in GH Actions use UTC; ART equivalent in a comment header on
      every workflow file.
- [x] Min-dwell time fences use `now() - state_entered_at` in UTC arithmetic; no TZ
      bug surface.

### Gate 4 — Provider Abstraction
- [x] N/A — no external AI calls in this module (stubs are pure-Python).

### Gate 5 — Determinism
- [x] The FSM pure function is deterministic: same `(current, trigger, now)` →
      same `Plan`. Property test in `test_cycle_fsm.py::test_purity`.
- [x] Stub side-effects are deterministic for reproducibility.

### Gate 6 — Spanish / English
- [x] FSM state names in code are in Spanish (`ESTRENO`, `RECEPCION_IDEAS`, etc.)
      because they are **domain terms** the PO uses to talk about the product. This
      is the explicit exception to Gate 6 "English in code"; documented in research
      R-009.
- [x] All other identifiers in English.

### Gate 7 — Soft Delete
- [x] N/A — no user-authored content here.

### Gate 8 — Tests from Day One
- [x] FSM pure function: 100 % branch coverage.
- [x] Executor: integration tests for happy path + 6 named failure modes
      (LockBusy, IllegalTransition, TimeFenceViolation, ReplayDetected,
      BackgroundTaskCrash, MissingSeason).
- [x] CLIs: smoke test per CLI.
- [x] Workflows: each `tick-*.yml` is unit-tested by `act` (locally; CI optional).

### Gate 9 — Trust Boundaries
- [x] HMAC validation unchanged (inherits from module 001).
- [x] `kill-switch` requires `ADMIN_TOKEN` (separate from `JWT_SECRET` and
      `TICK_SECRET`).
- [x] `health/cycle` is unauthenticated but exposes no secrets — only state names,
      timestamps, public ids.
- [x] Stub side-effects do not call any external service. No prompt-injection
      surface yet (it lands in module 006).

### Gate 10 — Observability
- [x] `state_transition` structured log on every transition.
- [x] `cycle_metrics` summary log emitted by watchdog with histogram of state
      durations.
- [x] `GET /internal/health/cycle` exposes the same data via JSON.

## Project Structure

### Documentation (this feature)

```text
specs/003-cycle-fsm/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── contracts/
│   └── cycle.yaml
├── quickstart.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### New / modified code

```text
apps/api/
├── alembic/versions/
│   └── 0004_seasons_chapters_cycles.py     ← NEW (consolidated)
├── app/
│   ├── domain/
│   │   ├── cycle_fsm.py                    ← NEW (pure)
│   │   ├── cycle_executor.py               ← NEW
│   │   ├── side_effects.py                 ← NEW (DI registry + stubs)
│   │   └── cycle_clock.py                  ← NEW (TZ-aware schedule helper)
│   ├── infra/
│   │   ├── cycles_repo.py                  ← NEW
│   │   ├── transitions_repo.py             ← NEW
│   │   ├── seasons_repo.py                 ← NEW
│   │   ├── chapters_repo.py                ← NEW
│   │   └── system_flags_repo.py            ← NEW
│   ├── api/
│   │   ├── internal_transition.py          ← MODIFIED (replace stub)
│   │   ├── internal_kill_switch.py         ← NEW
│   │   └── internal_health_cycle.py        ← NEW
│   ├── middleware/
│   │   └── admin_token.py                  ← NEW (for kill-switch)
│   └── scripts/
│       ├── bootstrap_cycle.py              ← NEW (CLI)
│       ├── replay_tick.py                  ← NEW (CLI)
│       └── kill_switch.py                  ← NEW (CLI)
└── tests/
    ├── unit/
    │   ├── test_cycle_fsm.py               ← exhaustive
    │   ├── test_cycle_clock.py
    │   └── test_side_effects_stubs.py
    └── integration/
        ├── test_transition_happy.py
        ├── test_transition_idempotency.py
        ├── test_transition_time_fence.py
        ├── test_transition_illegal.py
        ├── test_transition_lock_busy.py
        ├── test_kill_switch.py
        ├── test_health_cycle.py
        └── test_watchdog.py

.github/workflows/
├── tick-12-estreno.yml         ← MODIFIED (enable schedule:)
├── tick-18-vote.yml            ← MODIFIED
├── tick-23-generate.yml        ← MODIFIED
└── tick-2355-watchdog.yml      ← MODIFIED
```

## Phase 0 — Research

See [research.md](./research.md). Key decisions:

- **PG advisory locks** (not row locks, not Postgres-based queue).
- **FastAPI `BackgroundTasks` + `asyncio.create_task`** (not Celery/RQ/taskiq).
- **Default-deny on missing `trigger_id`** in `state_transitions`.
- **Watchdog: schedule-aware stuck detection** vs. blanket "any state stuck > N min".
- **Kill-switch auth: separate `ADMIN_TOKEN`** vs. user JWT vs. HMAC.
- **Bootstrap workflow**: declarative YAML manifest for season + chapter 0.
- **Cron jitter mitigation**: GH Actions `--retry 3` + watchdog idempotent re-fire.
- **Stuck-cycle recovery**: manual `replay-tick`, no auto-retry beyond watchdog escalation.
- **Spanish state names**: domain language exception to Gate 6.

## Phase 1 — Design Artefacts

- [contracts/cycle.yaml](./contracts/cycle.yaml).
- [data-model.md](./data-model.md).
- [quickstart.md](./quickstart.md).
- [checklists/requirements.md](./checklists/requirements.md).
- [tasks.md](./tasks.md).

## Phase 2 — Implementation Sequence

1. **T-001..T-002** — Migrations: `0004_seasons_chapters_cycles.py`,
   `0005_state_transitions.py`, `0006_system_flags.py`.
2. **T-003..T-005** — Pure FSM + clock + side-effects DI.
3. **T-006..T-010** — Infra repos (cycles, transitions, seasons, chapters,
   system_flags).
4. **T-011..T-013** — Stub side-effects (director_filter_stub, generation_pipeline_stub).
5. **T-014..T-016** — State executor (lock, transition, schedule task).
6. **T-017..T-019** — Replace `/internal/transition` body; ship `kill-switch` and
   `health/cycle` endpoints.
7. **T-020..T-022** — CLIs (`bootstrap-cycle`, `replay-tick`, `kill-switch`).
8. **T-023..T-026** — Enable the four `tick-*.yml` workflows; watchdog logic.
9. **T-027..T-029** — Integration test sweep + chaos tests (concurrent ticks,
   crash mid-flight).
10. **T-030** — Deploy + observe one full 24-h cycle on Fly with the stubs.

## Risks & Mitigations (feature-local)

| ID | Risk | Mitigation |
|---|---|---|
| **R-C1** | Advisory lock starvation if a task hangs holding the transaction | Tasks NEVER hold the lock across `await` boundaries that wait on external I/O. Pattern enforced by code review + a unit test. |
| **R-C2** | GH Actions cron misses by > 15 min | Watchdog at 23:55 catches missed 23:00; daily auto-recover. For 12:00 misses, document manual `replay-tick`. |
| **R-C3** | Background task crashes silently → cycle stuck in FILTERING/GENERACION | Wrap task body in `try/except Exception`; on crash, transition cycle to `FAILED` + Discord alert + kill-switch auto-on. |
| **R-C4** | Fly machine restart loses in-flight BackgroundTask | Watchdog detects state-stuck-too-long; admin runs `replay-tick`. Documented as known limitation. |
| **R-C5** | `system_flags` table contention (kill-switch read on every endpoint) | Cache in-process for 30 s. Acceptable freshness for the use-case. |
| **R-C6** | Migration 0004 takes locks on busy production DB | Closed beta = no concurrent writes; non-issue. Document as caveat for future. |

## Post-Conditions

After merge:
- The loop runs end-to-end (with stubs) for the duration of one calendar day.
- All future modules consume `cycles.state` to gate their behavior:
  - Module 004 (chapters-content) reads `cycle.state` to decide which chapter to
    expose.
  - Module 005 (twists-submission) gates by `state='RECEPCION_IDEAS'`.
  - Module 006 (director-filter) replaces `director_filter_stub` in the DI registry.
  - Module 007 (voting) gates by `state='VOTACION'`.
  - Module 008 (generation-pipeline) replaces `generation_pipeline_stub`.
