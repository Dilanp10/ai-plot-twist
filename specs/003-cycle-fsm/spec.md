# Feature Specification: Daily Cycle Finite State Machine

**Feature Branch**: `003-cycle-fsm`
**Created**: 2026-06-07
**Status**: Draft
**Depends on**: `001-project-bootstrap` (skeleton + HMAC tick stub + disabled cron workflows)

## Summary

Replace the HMAC-stubbed `POST /internal/transition` from module 001 with the **real**
state engine of the daily cycle. Flip the four `tick-*.yml` GitHub Actions workflows from
`workflow_dispatch`-only to scheduled. Ship `seasons`, `chapters`, `cycles`, and
`state_transitions` tables. Provide a kill-switch and a health endpoint for the cycle.

The FSM has eight states: `PENDING_RELEASE → ESTRENO → RECEPCION_IDEAS → FILTERING →
VOTACION → GENERACION → PENDING_RELEASE` (loop) and `FAILED` (terminal pending admin
intervention). Idempotency is enforced via `pg_advisory_xact_lock` on the cycle plus a
`UNIQUE (cycle_id, to_state, trigger_id)` constraint on `state_transitions`.

Modules 006 (director filter) and 008 (generation pipeline) own the real bodies of the
two side-effects fired during `FILTERING` and `GENERACION`. This module ships
**stub implementations** that succeed silently (approving all twists / cloning the
previous chapter manifest) so the loop can be exercised end-to-end before those modules
land.

## User Scenarios & Testing

### User Story 1 — Daily cycle advances autonomously (Priority: P1)

The deployed system, with the four cron workflows enabled, walks through the four
phases over 24 h without any human intervention.

**Why this priority**: this *is* the product loop. If it cannot run unattended for one
day, nothing else matters.

**Independent Test**: deploy the API, run `pnpm bootstrap-cycle --season s01
--day-zero-manifest cap0.yaml`, wait 24 h with cron enabled, observe four entries in
`state_transitions` matching the four daily ticks and a new `chapters` row created by
the generation stub.

**Acceptance Scenarios**:

1. **Given** a cycle in state `PENDING_RELEASE` and the cron tick at 12:00 ART arrives
   with a valid HMAC,
   **When** the state engine processes it,
   **Then** `cycles.state` becomes `ESTRENO`, `chapters.released_at` is set to `now()`,
   `chapters.status` becomes `live`, and a `state_transitions` row is inserted with
   `triggered_by='cron'`, `from_state='PENDING_RELEASE'`, `to_state='ESTRENO'`.

2. **Given** a cycle in `ESTRENO` and ≥ 60 s have elapsed since `state_entered_at`,
   **When** any subsequent inbound request (or the 12:01 internal auto-tick) is
   processed,
   **Then** the cycle auto-transitions to `RECEPCION_IDEAS` without a separate cron
   tick.

3. **Given** the 18:00 ART cron tick,
   **When** processed,
   **Then** the cycle transitions to `FILTERING`, a background task `director_filter`
   is spawned, and on its completion (stub: marks all pending twists `approved`) the
   cycle transitions to `VOTACION`.

4. **Given** the 23:00 ART cron tick,
   **When** processed,
   **Then** the cycle transitions to `GENERACION`, a background task
   `generation_pipeline` is spawned, and on its completion (stub: clones the live
   chapter's manifest into a new `chapters` row) the cycle transitions to
   `PENDING_RELEASE` with `next_chapter_id` set.

### User Story 2 — Replay of a cron tick is a no-op (Priority: P1)

GitHub Actions sometimes retries on transient failure. The same `trigger_id` arriving
twice MUST NOT advance the FSM twice.

**Why this priority**: silent double-advancement would mean two chapters published in
one day, the engagement loop breaks for everyone.

**Acceptance Scenarios**:

1. **Given** a `state_transitions` row already exists for `(cycle_id, to_state,
   trigger_id)`,
   **When** the same tick arrives again,
   **Then** the endpoint returns HTTP 200 (not 202) with body
   `{"status":"already_applied","applied_at":"<original_ts>"}` and no new row is
   inserted.

2. **Given** two `tick-12-estreno` workflow runs fire within the same 100 ms window
   for the same cycle (race),
   **When** both POST to `/internal/transition`,
   **Then** exactly one row appears in `state_transitions` (the loser blocks on
   the advisory lock then sees the winner's row and returns `already_applied`).

### User Story 3 — Invalid transitions are rejected (Priority: P1)

A tick targeting a state that is not legal from the current state must be rejected
without side-effects.

**Acceptance Scenarios**:

1. **Given** a cycle in `VOTACION`,
   **When** a tick payload `{to: "ESTRENO"}` arrives (illegal jump),
   **Then** HTTP 409 with `{"code":"illegal_transition", "from":"VOTACION",
   "to":"ESTRENO"}` and no DB write.

2. **Given** a cycle in `RECEPCION_IDEAS` and the 18:00 tick targets `FILTERING`,
   **When** the tick arrives at 17:59:00 (60 s before `min_dwell_time` is met),
   **Then** HTTP 409 with `{"code":"time_fence_violation","earliest_at":"..."}`.

### User Story 4 — Watchdog detects stuck cycle (Priority: P2)

The 23:55 ART watchdog tick inspects the cycle and either confirms healthy progress or
escalates.

**Acceptance Scenarios**:

1. **Given** the cycle is in `GENERACION` at 23:55 with `state_entered_at` 50 min ago
   and `next_chapter_id IS NULL`,
   **When** the watchdog tick runs,
   **Then** the endpoint logs `watchdog_check {state: "GENERACION", elapsed_min: 55,
   verdict: "ok_in_progress"}` and returns 200.

2. **Given** the cycle is still in `FILTERING` at 23:55 (filter stuck > 5 h),
   **When** the watchdog runs,
   **Then** it transitions the cycle to `FAILED`, posts to the configured Discord
   webhook with the cycle id, and returns 200.

3. **Given** the cycle is in `PENDING_RELEASE` at 23:55 (healthy — generation
   succeeded ahead of time),
   **When** the watchdog runs,
   **Then** verdict `ready_for_release` is logged, no state change.

### User Story 5 — Kill-switch freezes the loop (Priority: P2)

The PO can stop the loop with one CLI call. The next user-facing read sees a
maintenance banner; the cron ticks succeed but no-op.

**Acceptance Scenarios**:

1. **Given** the PO calls `pnpm kill-switch --on --reason "rebuild s01 bible"`,
   **When** any cron tick arrives,
   **Then** the endpoint returns 200 with `{"status":"kill_switch_active",
   "reason":"..."}` and no state change.

2. **Given** the kill-switch is active,
   **When** a user calls `GET /api/v1/chapters/today`,
   **Then** HTTP 503 with `{"code":"under_maintenance","reason":"..."}` (defined
   in module 004; this module just sets the global flag).

3. **Given** `pnpm kill-switch --off`,
   **When** the next cron tick arrives,
   **Then** the FSM resumes normally — the kill-switch does not auto-replay missed
   ticks; the operator is responsible for re-bootstrapping if needed.

### Edge Cases

- **Cron arrives during state side-effect**: e.g. 23:00 tick arrives at 23:00:00 but
  the FILTERING task spawned at 18:00 hasn't completed (long stuck filter). The 23:00
  tick MUST refuse to advance (still in `FILTERING`); it logs and returns
  `time_fence_violation`. The watchdog at 23:55 escalates to FAILED.
- **Side-effect crashes mid-flight**: e.g. `generation_pipeline` panics. A `try/except`
  wrapping the task sets cycle to `FAILED` and persists the traceback hash in
  `state_transitions.payload_json.error_hash`.
- **Clock skew > 300 s**: the HMAC middleware (from module 001) already rejects with
  409 — this module relies on that.
- **No active season**: every endpoint returns 503 with `{"code":"no_active_season"}`.
  The PO MUST run `pnpm bootstrap-cycle` to create one before cron runs.
- **PG advisory lock contention timeout**: if a transition can't acquire the lock in
  2 s, return 503 with `{"code":"lock_busy"}` and let GitHub Actions retry the workflow.
- **Cycle date rollover at midnight**: cycle.cycle_date is set at bootstrap; subsequent
  cycles increment by 1 day in ART. No DST surprises (ART has no DST).
- **Multiple concurrent FAILED transitions**: the kill-switch is set after the first
  FAILED to prevent thundering-herd retries. Manual unset by admin.

## Requirements

### Functional Requirements

- **FR-001**: Replace the HMAC stub in `POST /api/v1/internal/transition` with the real
  state engine. The endpoint MUST still validate HMAC and timestamp (±300 s) before
  doing anything else.
- **FR-002**: The state engine MUST acquire `pg_advisory_xact_lock(hashtext('cycle:' ||
  cycle.id))` at the top of every transition transaction. If acquisition exceeds 2 s,
  raise `LockBusy` → 503.
- **FR-003**: Every transition MUST insert one row in `state_transitions` within the
  same transaction. The UNIQUE constraint `(cycle_id, to_state, trigger_id)` MUST
  exist. A duplicate `trigger_id` insert MUST be caught and translated to a 200
  `already_applied` response (idempotency).
- **FR-004**: The FSM transitions table is the **single source of truth** for legal
  transitions. Implementations MUST reject any transition not in the table:

  | from | to | trigger source(s) |
  |---|---|---|
  | `PENDING_RELEASE` | `ESTRENO` | cron @ 12:00 |
  | `ESTRENO` | `RECEPCION_IDEAS` | cron @ 12:01 OR auto-tick after 60 s |
  | `RECEPCION_IDEAS` | `FILTERING` | cron @ 18:00 |
  | `FILTERING` | `VOTACION` | director_filter task complete |
  | `FILTERING` | `FAILED` | director_filter task failure (retries exhausted) |
  | `VOTACION` | `GENERACION` | cron @ 23:00 |
  | `GENERACION` | `PENDING_RELEASE` | generation_pipeline task complete |
  | `GENERACION` | `FAILED` | generation_pipeline timeout / failure |
  | any | any | admin override (kill-switch context) |

- **FR-005**: Min-dwell times per state (preventing premature transitions):

  | state | min_dwell |
  |---|---|
  | `PENDING_RELEASE` | 0 (release-now is valid) |
  | `ESTRENO` | 60 s |
  | `RECEPCION_IDEAS` | 5 h 30 min |
  | `FILTERING` | 1 s |
  | `VOTACION` | 4 h 45 min |
  | `GENERACION` | 30 min |

- **FR-006**: `POST /api/v1/internal/kill-switch` MUST accept `{on: bool, reason:
  string}` and be protected by `Authorization: Bearer <ADMIN_TOKEN>` (separate from
  user JWT). State persists in a `system_flags` table.
- **FR-007**: `GET /api/v1/internal/health/cycle` MUST return current state, time in
  state, last 5 transitions, kill-switch status, and the cron-ticks expected in the
  next 24 h.
- **FR-008**: The four GitHub Actions workflows (`tick-12-estreno`, `tick-18-vote`,
  `tick-23-generate`, `tick-2355-watchdog`) MUST be flipped from `workflow_dispatch`
  only to scheduled cron expressions, with `workflow_dispatch` still enabled for
  manual replay.
- **FR-009**: The state engine MUST spawn `director_filter(chapter_id)` as a FastAPI
  `BackgroundTask` on `FILTERING` entry. Module 003 ships a stub implementation
  (`director_filter_stub`) that marks all `pending_review` twists as `approved` and
  transitions to `VOTACION`. The real implementation is injected by module 006 via
  a DI registry.
- **FR-010**: Likewise, `generation_pipeline(chapter_id)` is spawned on `GENERACION`
  entry. Module 003 ships a stub (`generation_pipeline_stub`) that creates the next
  `chapters` row by cloning the live chapter's `manifest_json` and incrementing
  `day_index`. Real impl in module 008.
- **FR-011**: `pnpm bootstrap-cycle --season SLUG --day-zero-manifest FILE.yaml` MUST
  read the YAML manifest, INSERT the season + first chapter (`day_index=1`,
  `status='ready'`), create the first cycle in state `PENDING_RELEASE`, and exit 0.
  Refuses if another active season exists (unless `--force-replace`).
- **FR-012**: `pnpm replay-tick --to STATE --cycle-id N` MUST POST a synthetic tick
  to `/internal/transition` with a fresh `trigger_id` (`local-replay-<uuid>`). Used
  for manual recovery from FAILED.
- **FR-013**: `pnpm kill-switch --on|--off [--reason TEXT]` MUST set the flag and
  print the new state.
- **FR-014**: All four `tick-*.yml` workflows MUST send `{trigger_id: GITHUB_RUN_ID,
  ts: <epoch>, to: <state>, retry_attempt: GITHUB_RUN_ATTEMPT}` and use `--retry 3
  --retry-delay 10` on the curl. The endpoint MUST honor `retry_attempt > 1` by
  short-circuiting if the prior attempt already succeeded.
- **FR-015**: A structured log event MUST be emitted on every transition:
  `state_transition {cycle_id, from_state, to_state, triggered_by, trigger_id,
  duration_ms, outcome}`.
- **FR-016**: The state engine MUST be implemented as a pure function
  `compute_next_state(current, ctx) -> Plan` (no DB I/O) plus a thin executor that
  performs the I/O. The pure function is unit-tested exhaustively (all legal +
  illegal pairs).

### Non-Functional Requirements

- **NFR-001**: Transition latency p95 < 200 ms (excluding background task duration).
- **NFR-002**: Replay-detection (already-applied path) p95 < 50 ms.
- **NFR-003**: The state engine MUST tolerate up to 5 concurrent ticks for the same
  cycle (advisory lock serializes; max wait 2 s; no deadlock).
- **NFR-004**: GitHub Actions cron jitter tolerance: a tick arriving up to 15 min
  late MUST still succeed (min_dwell guards prevent only *early* ticks).

### Out of Scope (for this feature)

- Real director filter (LLM moderation) — module 006.
- Real generation pipeline (scriptwriter LLM + T2I + R2 upload) — module 008.
- User-facing chapter content delivery (`GET /chapters/today`) — module 004.
- Per-season config (multiple seasons in parallel) — out of MVP scope.
- ADMIN_TOKEN rotation / multi-admin — single shared secret in MVP.
- Discord webhook signing — best-effort, no auth on the outbound message.
