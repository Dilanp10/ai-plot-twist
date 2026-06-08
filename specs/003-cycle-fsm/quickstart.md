# Quickstart: Daily Cycle FSM

**Branch**: `003-cycle-fsm` | **Date**: 2026-06-07
**Depends on**: modules 001 + 002 merged. Postgres running. API up on `:8000`.

End-to-end recipe: bootstrap a season, observe a full FSM cycle locally by force-firing
each tick, exercise idempotency, kill-switch, watchdog.

---

## 1. Apply the new migrations

```sh
pnpm migrate
# alembic upgrade head → 0001 .. 0006
```

Verify the five new tables exist:

```sh
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist \
  -c "\dt seasons|chapters|cycles|state_transitions|system_flags"
```

---

## 2. Set the new secrets in `.env.local`

```ini
# .env.local additions
ADMIN_TOKEN=$(openssl rand -base64 32)
DISCORD_WEBHOOK_URL=                   # optional; leave empty in dev
```

Restart the API.

---

## 3. Bootstrap the first season + chapter 0

Create `seed/cap0.yaml` from the template in `docs/seed/example-cap0.yaml`. Then:

```sh
pnpm bootstrap-cycle --season s01-el-tunel --day-zero-manifest seed/cap0.yaml
```

Expected output:

```
Reading manifest seed/cap0.yaml ... OK
Validating against ChapterZeroSchema ... OK
Inserted season s01-el-tunel (id=1).
Inserted chapter 1 (day_index=1, status='ready').
Created cycle 1 in state PENDING_RELEASE, cycle_date=2026-06-08.

Next tick expected at 2026-06-08T12:00:00-03:00 (ESTRENO).
```

Verify in DB:

```sh
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist \
  -c "SELECT id, state, cycle_date FROM cycles;"
```

---

## 4. Force-fire the four daily ticks (local dev replay)

Use `pnpm replay-tick` so you don't have to compute HMAC by hand.

### 4.1 ESTRENO

```sh
pnpm replay-tick --to ESTRENO
```

Expected: HTTP 202, cycle.state=ESTRENO, chapter.released_at set, chapter.status=live.

```sh
curl -sS http://localhost:8000/api/v1/internal/health/cycle | jq
# cycle.state should be ESTRENO with time_in_state_seconds growing.
```

### 4.2 Wait 60 s (or skip with `--no-dwell-check` locally)

Then re-trigger — the engine auto-advances to RECEPCION_IDEAS:

```sh
pnpm replay-tick --to RECEPCION_IDEAS
```

### 4.3 FILTERING (18:00 tick)

```sh
pnpm replay-tick --to FILTERING --no-dwell-check
```

The stub `director_filter_stub` runs in the background; expected: ~100 ms later the
cycle is in `VOTACION`.

Tail the API log:

```
{"event":"state_transition","cycle_id":1,"from":"RECEPCION_IDEAS","to":"FILTERING",...}
{"event":"side_effect_started","name":"director_filter_stub",...}
{"event":"side_effect_done","name":"director_filter_stub","approved":0,...}
{"event":"state_transition","cycle_id":1,"from":"FILTERING","to":"VOTACION",...}
```

### 4.4 GENERACION (23:00 tick)

```sh
pnpm replay-tick --to GENERACION --no-dwell-check
```

The stub `generation_pipeline_stub` clones the live chapter into a new `chapters` row
(`day_index=2`, `status='ready'`) and transitions the cycle to `PENDING_RELEASE`.

```sh
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist \
  -c "SELECT id, day_index, status FROM chapters;"
# Expect 2 rows now: day 1 (live) and day 2 (ready).
```

### 4.5 Loop closes at next 12:00

```sh
pnpm replay-tick --to ESTRENO
```

Now day-1 archives and day-2 goes live.

---

## 5. Exercise idempotency

Replay the same tick twice with the SAME `trigger_id`:

```sh
pnpm replay-tick --to FILTERING --trigger-id smoke-1
pnpm replay-tick --to FILTERING --trigger-id smoke-1
# First: 202 accepted
# Second: 200 already_applied with the original transition_id and applied_at
```

DB check — only ONE row should exist:

```sh
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist \
  -c "SELECT COUNT(*) FROM state_transitions WHERE trigger_id='smoke-1';"
# Expect: 1
```

---

## 6. Exercise illegal transitions

```sh
# From VOTACION → ESTRENO is illegal
pnpm replay-tick --to ESTRENO
# 409 illegal_transition
```

---

## 7. Exercise time fence

Right after entering `RECEPCION_IDEAS`, attempt to advance immediately:

```sh
pnpm replay-tick --to FILTERING
# 409 time_fence_violation, earliest_at=... (5h30m ahead)
```

Bypass for local testing:

```sh
pnpm replay-tick --to FILTERING --no-dwell-check
```

---

## 8. Kill-switch

Activate:

```sh
pnpm kill-switch --on --reason "ajustando la bible de la temporada"
# → kill_switch.on = true, reason = "ajustando la bible de la temporada"
```

Any subsequent tick:

```sh
pnpm replay-tick --to ESTRENO
# 409 kill_switch_active
```

Deactivate:

```sh
pnpm kill-switch --off
# → kill_switch.on = false
```

Verify via health endpoint:

```sh
curl -sS http://localhost:8000/api/v1/internal/health/cycle | jq '.kill_switch'
```

---

## 9. Watchdog (force-fire WATCHDOG tick)

```sh
pnpm replay-tick --to WATCHDOG
```

Expected output: 202 with `verdict` field in body indicating what the watchdog
concluded. No state change unless the cycle is genuinely stuck.

Simulate a stuck cycle: manually rewind `state_entered_at`:

```sh
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist \
  -c "UPDATE cycles SET state='FILTERING', state_entered_at=now() - interval '6 hours' WHERE id=1;"
```

Run watchdog:

```sh
pnpm replay-tick --to WATCHDOG
# Expect: verdict=stuck_filtering, cycle.state transitioned to FAILED, kill_switch auto-on.
```

Recover:

```sh
pnpm kill-switch --off
# Manually transition back to VOTACION
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist \
  -c "UPDATE cycles SET state='RECEPCION_IDEAS', state_entered_at=now() WHERE id=1;"
```

---

## 10. Enable the scheduled cron workflows

After local smoke-test passes, push the four updated `.github/workflows/tick-*.yml`
files (the `schedule:` block is now uncommented and `workflow_dispatch` remains for
manual replay). Verify in the GitHub UI that each workflow shows the next scheduled
run.

Required GitHub repo secrets:

| Secret | Value |
|---|---|
| `API_URL` | `https://<your-app>.fly.dev` |
| `TICK_SECRET` | matches Fly secret |

---

## 11. Deploy + observe one day

```sh
fly deploy --config infra/fly.toml
```

Watch the `tick-12-estreno` workflow at 12:00 ART (15:00 UTC) in GitHub Actions.
Confirm a `state_transitions` row appears via `GET /internal/health/cycle`.

Repeat checks at 18:00, 23:00, 23:55.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `bootstrap-cycle` fails `another active season exists` | A previous run left a season `is_active=TRUE` | Use `--force-replace` (development only); never in prod |
| Tick returns 409 `illegal_transition` and you're sure it should be legal | Cycle is in `FAILED` | Inspect via `health/cycle`; recover manually then re-fire |
| Tick returns 409 `time_fence_violation` locally | Min-dwell guard | `--no-dwell-check` for dev |
| Tick returns 503 `lock_busy` | Another transaction holding the cycle lock | Wait; if persistent, restart the API (in-flight task held the lock — bug to file) |
| Watchdog never escalates | Discord webhook unset (`DISCORD_WEBHOOK_URL=""`) | Set the env var; alerts go to stdout if empty |
| Cycle never auto-advances from `ESTRENO` to `RECEPCION_IDEAS` | The 60 s auto-tick is a passive guard; the engine evaluates it on the next tick OR `health/cycle` call | Hit `health/cycle` to force the lazy advance, or wait for the next tick |
| Generation stub creates chapters with empty manifest | Expected; the stub clones the live chapter's manifest verbatim. Real content lands with module 008 |
