# ADR-0006 — Push Fan-out Hooked onto ESTRENO Transition

**Status**: Accepted  
**Date**: 2026-06-15  
**Deciders**: Dilan Perea (PO + lead dev)  
**Links**: Research R-007 (`specs/011-web-push/research.md#r-007`), Module 011 T-010

---

## Context

The Web Push fan-out (module 011) must run once per chapter release — that
is, whenever the cycle FSM transitions `PENDING_RELEASE → ESTRENO`.

Two integration points were evaluated:

**Option A — Hook into the executor's side-effect dispatch mechanism**  
The FSM edge table in `cycle_fsm.py` already carries a `side_effect` field.
Setting it to `"push_fanout"` for the `PENDING_RELEASE → ESTRENO` edge causes
the executor to return `side_effect_name="push_fanout"` in `TransitionResult`,
and the existing HTTP layer (`internal_transition.py`) then spawns it as a
FastAPI `BackgroundTask`. No new code path is needed.

**Option B — Explicit call inside the executor body**  
Add a direct call to `run_push_fanout` inside `cycle_executor.transition()`,
bypassing the side-effect registry.

## Decision

**Option A** — use the existing side-effect dispatch mechanism.

## Rationale

- **Zero new code paths**: the executor → HTTP → BackgroundTask plumbing is
  already tested by module 003's integration suite. Adding `"push_fanout"` to
  the edge table is a one-field change.
- **Symmetry**: `director_filter` (RECEPCION_IDEAS→FILTERING) and
  `generation_pipeline` (VOTACION→GENERACION) already follow this pattern.
  Option A keeps all side-effect dispatch visually consistent and discoverable
  from one place (`cycle_fsm._EDGE`).
- **Best-effort framing**: `BackgroundTask` means the push dispatch does not
  block or fail the transition HTTP response. If the fan-out crashes, module
  003's `safe_side_effect` wrapper logs + alerts without rolling back the
  already-committed state change — matching FR-009 ("best-effort").
- **DI-friendly**: `main.py` registers the real `push_fanout` implementation
  when VAPID keys are present; the no-op stub stays in place otherwise. This
  follows the same degraded-mode pattern as the director filter and generation
  pipeline.

## Consequences

- `cycle_fsm._EDGE[("PENDING_RELEASE", "ESTRENO")]` now carries
  `"push_fanout"` as the side effect.
- `TransitionResult.side_effect_name` is `"push_fanout"` for every successful
  ESTRENO transition — callers that previously checked for `None` must update.
- `main.py._wire_push_fanout` registers the real sender at startup when
  `VAPID_PRIVATE_KEY` and `VAPID_PUBLIC_KEY` are set.
