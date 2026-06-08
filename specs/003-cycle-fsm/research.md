# Phase 0 Research: Daily Cycle FSM

**Branch**: `003-cycle-fsm` | **Date**: 2026-06-07

Mini-ADRs for each non-trivial decision in this module.

---

## R-001 — Concurrency primitive for cycle mutex

**Question**: How do we serialize state transitions against concurrent ticks for the
same cycle?

| Option | Pros | Cons |
|---|---|---|
| Row lock (`SELECT … FOR UPDATE`) | Standard SQL, well understood | Locks the `cycles` row for the entire transaction — blocks readers that join `cycles` |
| **PG advisory lock (`pg_advisory_xact_lock`) (chosen)** | Lightweight, namespaced, releases on transaction end automatically, no read interference | Less familiar to some devs; numeric key (we hash a string) |
| Application-level lock (in-process mutex) | Simplest | Doesn't work across Fly machine restarts or future scale-out |
| Postgres LISTEN/NOTIFY queue | Decoupled | Wrong abstraction for "at-most-one-write" |

**Decision**: **`pg_advisory_xact_lock(hashtext('cycle:' || cycle.id))`** acquired at
the top of every transition transaction. 2 s timeout via `SET LOCAL
lock_timeout = '2000ms'` before acquiring. On timeout, raise `LockBusy` → 503.

**Rationale**: this is exactly what advisory locks were designed for. They release
automatically on COMMIT/ROLLBACK, don't interfere with concurrent reads of `cycles`,
and the key namespace (`hashtext('cycle:' || id)`) ensures different cycles never
collide.

**Trigger to revisit**: never expected; advisory locks are a stable PG primitive.

---

## R-002 — Background task scheduler

**Question**: FastAPI `BackgroundTasks`, Celery, RQ, taskiq, or in-app `asyncio`?

| Option | Pros | Cons |
|---|---|---|
| **FastAPI `BackgroundTasks` + `asyncio.create_task` (chosen)** | Zero deps, zero infra | Lost on process restart; no retries; no observability beyond logs |
| Celery + Redis | Mature, retries, scheduling | Requires Redis (paid), separate worker process, more infra |
| RQ + Redis | Simpler than Celery | Same Redis issue |
| taskiq + PG broker | PG as broker (cool) | Adds a dep + a worker process |
| APScheduler in-process | Persistent jobstore in PG | Conflicts conceptually with GH Actions as the cron source; double-scheduling risk |

**Decision**: **FastAPI BackgroundTasks** + `asyncio.create_task` for richer-lifecycle
needs. The watchdog at 23:55 catches anything lost to process restarts.

**Rationale**: we have exactly two background side-effects, both bounded ≤ 1 h, both
idempotent on re-fire (via `trigger_id`). Adding Redis or a worker process violates
Gate 1 (zero-cost) or adds operational surface for marginal gain. The watchdog is
our safety net.

**Trigger to revisit**: any week with > 1 lost background task. We'd then evaluate
taskiq with PG broker (Redis-free).

---

## R-003 — Missing `trigger_id` policy

**Question**: A POST to `/internal/transition` arrives without a `trigger_id`. What
do we do?

| Option | Behavior |
|---|---|
| Default-allow | Generate a UUID server-side, proceed |
| **Default-deny (chosen)** | Reject with 422 `missing_trigger_id` |
| Quasi-deny | Accept only from `localhost` (for `pnpm replay-tick`) |

**Decision**: **default-deny**. Every legitimate caller (GitHub Actions, `pnpm
replay-tick`) can and does supply a `trigger_id`. Accepting requests without one
silently disables idempotency.

`pnpm replay-tick` generates `local-replay-<uuid>` server-side BEFORE the request
goes out, so it's still default-deny compliant.

---

## R-004 — Watchdog: stuck-state detection

**Question**: At 23:55 ART, the watchdog inspects the cycle. What does "stuck" mean?

**Decision**: schedule-aware verdict. The watchdog computes the expected state for
the current time-of-day and compares:

| Time-of-day (ART) | Expected state | If state is X… | Verdict |
|---|---|---|---|
| 23:55 | `PENDING_RELEASE` or `GENERACION` (almost done) | `PENDING_RELEASE` | `ready_for_release` |
| 23:55 | (same) | `GENERACION` with elapsed < 60 min | `ok_in_progress` |
| 23:55 | (same) | `GENERACION` with elapsed ≥ 60 min | `stuck_generation` → FAILED |
| 23:55 | (same) | `VOTACION` | `stuck_voting` → FAILED |
| 23:55 | (same) | `FILTERING` | `stuck_filtering` → FAILED |
| 23:55 | (same) | `RECEPCION_IDEAS` | `stuck_reception` → FAILED |
| 23:55 | (same) | `FAILED` | `already_failed` (no action) |
| 23:55 | (same) | `ESTRENO` | `impossible_state` → FAILED + critical alert |

Verdicts other than `ok_in_progress`/`ready_for_release`/`already_failed` trigger a
**Discord webhook post** with the cycle id, state, elapsed time, and a `pnpm
replay-tick` command to copy-paste.

**Trigger to revisit**: when the actual failure modes seen in beta differ from this
table.

---

## R-005 — Kill-switch auth

**Question**: How is `POST /internal/kill-switch` authenticated?

| Option | Pros | Cons |
|---|---|---|
| User JWT | Reuse existing identity | No admin/user distinction; would need a role field |
| HMAC (like tick) | Reuse pattern | Less convenient (need to compute HMAC by hand) |
| **Separate `ADMIN_TOKEN` bearer (chosen)** | Easy to use from a script, separate from user trust boundary | Yet another secret to manage |
| IP allow-list | Simplest | GH Actions IPs are huge; PO's home IP is dynamic |

**Decision**: **separate `ADMIN_TOKEN`**, set as a Fly secret. The PO has it in their
`.env.local`. CLI sends as `Authorization: Bearer $ADMIN_TOKEN`.

**Rotation**: change the Fly secret; the old token immediately stops working. No
audit trail in MVP beyond logs.

---

## R-006 — Bootstrap manifest format

**Question**: How does the PO seed the first season + chapter 0?

**Decision**: **YAML manifest** consumed by `pnpm bootstrap-cycle --season s01
--day-zero-manifest cap0.yaml`. Shape:

```yaml
season:
  slug: s01-el-tunel
  title: "El Túnel"
  bible:
    setting: "Buenos Aires, 1998, realismo mágico."
    tone: ["misterio", "intimista", "humor seco"]
    characters:
      - name: Mariana
        archetype: "investigadora obsesiva"
      - name: el Espejo
        archetype: "entidad ambigua"
    rules:
      - "Lo sobrenatural nunca se explica."
      - "Nadie usa celular después de las 18:00."
chapter_zero:
  day_index: 1
  title: "Lo que había detrás del espejo"
  synopsis: "Mariana cruza el umbral y descubre que…"
  panels:
    - idx: 1
      image_url: "https://assets.aiplottwist.example/s01/d01/panel_1.webp"
      narration: "El espejo crujió como hielo viejo…"
      mood: tense
    - idx: 2
      image_url: "https://assets.aiplottwist.example/s01/d01/panel_2.webp"
      narration: "…"
      mood: ominous
    # 3-4 panels total
  cliffhanger: "Una voz —la suya— le respondió desde el otro lado."
  next_cliffhanger_seed: "El espejo está roto pero la voz sigue."
```

**Rationale**: YAML is the right balance between PO-editable and structured. The
script validates against a Pydantic schema before any DB write.

**Trigger to revisit**: if the PO requests a web UI for season setup. Not in MVP.

---

## R-007 — Cron jitter mitigation

**Question**: GitHub Actions can delay a `schedule:` trigger by up to 15 minutes
during peak hours. How do we tolerate this?

**Decision**:

1. **Min-dwell guards** in FR-005 prevent *early* ticks but allow *late* ones —
   late is the actual jitter direction.
2. Each `tick-*.yml` calls `curl --retry 3 --retry-delay 10` — covers transient
   network blips.
3. The 23:55 watchdog catches a missed 23:00 (forces FAILED + alert if generation
   never started).
4. For a missed 12:00 (estreno), the PO runs `pnpm replay-tick --to ESTRENO`
   manually. Documented in quickstart.

**Trigger to revisit**: if missed-12:00 happens > once a month. Move to Cloudflare
Workers Cron Triggers (free, more reliable timing).

---

## R-008 — Side-effect failure recovery

**Question**: A background task (filter or generation) crashes. What happens?

**Decision**: the side-effect entry point is wrapped:

```python
async def safe_side_effect(name, fn, *args, **kwargs):
    try:
        await fn(*args, **kwargs)
    except Exception as e:
        await transitions_repo.force_state(cycle_id, "FAILED",
            payload={"error_hash": short_hash(str(e)),
                     "error_type": type(e).__name__,
                     "side_effect": name})
        await system_flags_repo.set("kill_switch",
            {"on": True, "reason": f"side_effect_failed:{name}"})
        await discord_alert(name, cycle_id, str(e)[:500])
        log.exception("side_effect_failed", side_effect=name)
```

Key choices:

- **Auto kill-switch on**: prevents a thundering herd of retries while the PO is
  asleep.
- **Discord webhook**: free, single-channel, with a copy-pasteable recovery command.
- **`error_hash`**: short hash of the error message stored in `state_transitions.payload_json`
  for correlation; the full message goes to logs only (not DB) to avoid PII leaks.

**Trigger to revisit**: if the auto-kill-switch annoys the PO. Compromise: only
auto-on for the second failure within 1 h.

---

## R-009 — Spanish state names in code (Gate 6 exception)

**Question**: Gate 6 says identifiers in code MUST be English. The FSM states are
named in Spanish (`ESTRENO`, `RECEPCION_IDEAS`, etc.). Is this OK?

**Decision**: **explicit exception**, documented here.

**Rationale**: these are **domain terms** that the PO uses in product conversations,
analytics, and the constitution. Translating them ("PREMIERE", "IDEA_COLLECTION",
"VOTING", "GENERATION") creates a permanent translation tax on every conversation
between code and product. Domain-Driven Design (Evans) calls this the *ubiquitous
language*; consistency between the spoken and written word here is more valuable
than the code-language convention.

**Boundary**: only the *FSM state names* are Spanish. Field names, function names,
variables, comments, log keys remain English. So `cycle.state = "ESTRENO"` is fine
but `cycle.estado = "ESTRENO"` is not.

**Recorded in**: the constitution Gate 6 will get a footnote in its next amendment
referring to this ADR.

---

## R-010 — Cleanup of stale state_transitions

**Question**: `state_transitions` grows unboundedly. When do we prune?

**Decision**: don't prune in MVP. Expected growth ≈ 4 rows/day × ~100 days = 400
rows by end of beta. Well within Neon free tier. A pruning policy lands when the
table exceeds 10 000 rows; archive older rows to R2 as JSONL.

---

## Open items (carried to follow-up modules)

- **OQ-FSM-1**: should `kill-switch` auto-off after N hours? Defer.
- **OQ-FSM-2**: should we expose the FSM diagram as Mermaid in the health endpoint
  for debugging? Nice-to-have.
- **OQ-FSM-3**: multi-machine deployment of the API (currently 1 Fly machine).
  Advisory locks survive across connections, so no migration needed; but background
  tasks running on different machines need coordination. Defer until scale demands.
