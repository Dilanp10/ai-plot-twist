# Requirements Checklist: Daily Cycle FSM

**Branch**: `003-cycle-fsm` | **Date**: 2026-06-07

A PR closing module 003 is mergeable only when every box below is ticked.

---

## Functional Requirements

- [ ] **FR-001** — `POST /internal/transition` stub from 001 fully replaced with the
      state engine. HMAC validation preserved.
- [ ] **FR-002** — Advisory lock `pg_advisory_xact_lock` acquired with 2 s timeout;
      `LockBusy` → 503. Tested in `test_transition_lock_busy.py` with two concurrent
      writers.
- [ ] **FR-003** — UNIQUE constraint `(cycle_id, to_state, trigger_id)` exists; replay
      returns 200 `already_applied`. Tested in `test_transition_idempotency.py` with
      50 concurrent identical ticks → exactly 1 row inserted, 50 responses (1×202 +
      49×200).
- [ ] **FR-004** — FSM transition table implemented as a pure dict in
      `app/domain/cycle_fsm.py`. Property test verifies illegal pairs all rejected.
- [ ] **FR-005** — Min-dwell times honored; `time_fence_violation` 409 with
      `earliest_at` field on early ticks. Six unit tests, one per state with non-zero
      dwell.
- [ ] **FR-006** — `POST /internal/kill-switch` requires `ADMIN_TOKEN`. Setting
      `on=true` makes subsequent ticks 409 `kill_switch_active`.
- [ ] **FR-007** — `GET /internal/health/cycle` returns documented JSON shape. All
      fields present.
- [ ] **FR-008** — Four `tick-*.yml` workflows have `schedule:` enabled AND
      `workflow_dispatch:` enabled. Cron expressions in UTC with ART comment.
- [ ] **FR-009** — `director_filter_stub` runs on `FILTERING` entry, approves all
      pending twists, transitions to `VOTACION`.
- [ ] **FR-010** — `generation_pipeline_stub` runs on `GENERACION` entry, clones the
      live chapter manifest into a new `chapters` row, transitions to
      `PENDING_RELEASE`.
- [ ] **FR-011** — `pnpm bootstrap-cycle` parses YAML, validates via Pydantic, inserts
      season + chapter + cycle atomically.
- [ ] **FR-012** — `pnpm replay-tick` posts a synthetic HMAC-signed tick locally.
      Supports `--no-dwell-check` (rejected in prod via `ENV=prod` guard).
- [ ] **FR-013** — `pnpm kill-switch` flips the flag and prints new state.
- [ ] **FR-014** — Workflows use `curl --retry 3 --retry-delay 10` and send
      `retry_attempt` from `GITHUB_RUN_ATTEMPT`. Endpoint short-circuits on
      retry of already-applied tick.
- [ ] **FR-015** — `state_transition` structured log emitted on every transition with
      the documented keys.
- [ ] **FR-016** — `compute_next_state` is pure (no DB import, no `now()` call).
      Tested in `test_cycle_fsm.py::test_purity` by mocking `time.time` and
      asserting deterministic output for 100 trials.

## Non-Functional Requirements

- [ ] **NFR-001** — Transition p95 < 200 ms (k6 attached).
- [ ] **NFR-002** — `already_applied` p95 < 50 ms.
- [ ] **NFR-003** — 5 concurrent ticks for same cycle serialize cleanly without
      deadlock; max wait observed ≤ 2 s.
- [ ] **NFR-004** — A tick arriving up to 15 min after scheduled time still succeeds.
      Tested with `freezegun`.

## Constitution Gates

- [ ] **Gate 1 — Zero-cost** — No new paid services.
- [ ] **Gate 2 — Idempotency** — Advisory lock + UNIQUE constraint together
      guarantee at-most-once side effects per `trigger_id`.
- [ ] **Gate 3 — TZ anchoring** — Schedules anchored to ART. `cycle_clock.py`
      uses `ZoneInfo("America/Argentina/Buenos_Aires")` exclusively. CI runs the
      test suite with `TZ=UTC` to catch hidden assumptions.
- [ ] **Gate 4 — Provider abstraction** — N/A.
- [ ] **Gate 5 — Determinism** — FSM pure; stubs deterministic; tiebreak rules
      (will land with module 008) don't appear in this module.
- [ ] **Gate 6 — Spanish UI, English code** — Exception logged in research R-009:
      FSM state names are Spanish as ubiquitous-language domain terms. Constitution
      footnote ADR linked in PR.
- [ ] **Gate 7 — Soft delete** — N/A.
- [ ] **Gate 8 — Tests from day one** — Full coverage: pure FSM 100 % branch, executor
      6 named failure modes, CLI smoke per script, workflow lint via `actionlint`.
- [ ] **Gate 9 — Trust boundaries** — HMAC unchanged. ADMIN_TOKEN distinct from
      JWT_SECRET and TICK_SECRET. Health endpoint exposes no secrets.
- [ ] **Gate 10 — Observability** — `state_transition`, `side_effect_started`,
      `side_effect_done`, `watchdog_check` log events documented and emitted.

## Documentation

- [ ] Quickstart walked end-to-end on a clean dev box.
- [ ] `docs/seed/example-cap0.yaml` shipped as a template.
- [ ] `specs/README.md` marks module `done`; marks 004 `in-progress`.
- [ ] ADR file `docs/adr/0001-spanish-fsm-state-names.md` exists and links to
      research R-009.

## Sign-off

- [ ] Reviewer 1 (engineering)
- [ ] Reviewer 2 (PO)
- [ ] Constitution amendment (Gate 6 footnote) merged? Link to PR.
