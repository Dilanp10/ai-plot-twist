# Quickstart: Director's Filter

**Branch**: `006-directors-filter` | **Date**: 2026-06-07
**Depends on**: modules 001 + 002 + 003 + 005 merged. API running.

---

## 1. Set the LLM credentials

In `.env.local`:

```ini
GEMINI_API_KEY=<your-free-tier-key>           # https://aistudio.google.com/apikey
GITHUB_MODELS_TOKEN=<github-pat-with-models-read>   # https://github.com/settings/personal-access-tokens
LLM_PRIMARY_MODEL=gemini-2.0-flash
LLM_FALLBACK_MODEL=gpt-4o-mini
DIRECTOR_BATCH_SIZE=25
```

Restart the API.

Sanity-check provider health:

```sh
curl -sS http://localhost:8000/api/v1/internal/health/cycle | jq '.providers // empty'
# (In MVP we don't surface provider health on this endpoint; check logs below.)
```

Tail the startup log; you should see:

```
{"event":"llm_provider_registered","name":"gemini","model":"gemini-2.0-flash"}
{"event":"llm_provider_registered","name":"github_models","model":"gpt-4o-mini"}
{"event":"director_filter_registered_di","over":"director_filter_stub"}
```

---

## 2. Seed `pending_review` twists

Use the module 005 quickstart (sections 1–5) to onboard a user and submit 3
twists for the current live chapter.

For variety, mix one obviously coherent twist, one nonsensical, and one with a
slur (use a placeholder like `xxxOFFENSIVExxx` if you don't want to type a real
one — the slur list constant can include the placeholder in dev).

Confirm:

```sh
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist \
  -c "SELECT public_id, status, LEFT(content,40) FROM twists WHERE chapter_id=(SELECT id FROM chapters WHERE status='live');"
# Expect 3 rows, all status=pending_review
```

---

## 3. Force the 18:00 transition

```sh
pnpm replay-tick --to FILTERING --no-dwell-check
```

In the API logs, expect this sequence:

```
{"event":"state_transition","from":"RECEPCION_IDEAS","to":"FILTERING","triggered_by":"cron"}
{"event":"side_effect_started","name":"director_filter","chapter_id":1}
{"event":"filter_started","chapter_id":1,"twist_count":3}
{"event":"llm_batch","batch_idx":0,"provider":"gemini","model":"gemini-2.0-flash","latency_ms":2104,"tokens_in":612,"tokens_out":188}
{"event":"slur_override_applied","twist_public_id":"...","reason":"Post-filter"}
{"event":"filter_completed","chapter_id":1,"approved":1,"rejected_offensive":1,"rejected_incoherent":1,"rejected_spam":0,"default_denied":0,"duration_ms":2400}
{"event":"state_transition","from":"FILTERING","to":"VOTACION","triggered_by":"side_effect"}
```

Verify DB:

```sh
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist \
  -c "SELECT public_id, status, director_reason FROM twists WHERE chapter_id=(SELECT id FROM chapters WHERE status='live');"
# Expect: 1 approved, 1 rejected_offensive (slur), 1 rejected_incoherent. Each with a reason.
```

---

## 4. Test Gemini failover to GitHub Models

Sabotage Gemini in `.env.local`:

```ini
GEMINI_API_KEY=invalid-on-purpose
```

Restart the API. Replay the filter for the current chapter:

```sh
pnpm rerun-filter --chapter-id $(docker exec -i $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist -tA -c "SELECT public_id FROM chapters WHERE status='live';")
```

Logs should show:

```
{"event":"llm_provider_failover","from":"gemini","to":"github_models","reason":"unauthorized"}
{"event":"llm_batch","batch_idx":0,"provider":"github_models","model":"gpt-4o-mini",...}
{"event":"filter_completed",...}
```

Restore the real key.

---

## 5. Test all-providers-down

Sabotage both keys, then trigger:

```sh
pnpm rerun-filter --chapter-id <uuid>
```

Expect HTTP 500 from the admin endpoint and a Discord webhook alert (if
configured). The original filter run that was triggered by the FSM (not the
replay) would have transitioned the cycle to `FAILED`; replay is independent
and does not touch state.

Restore keys, fix any cycle state via `pnpm replay-tick --to VOTACION
--no-dwell-check` if needed.

---

## 6. Test empty batch

Manually clear pending twists (after a filter run, this is naturally true), then
force the transition again on a fresh chapter / cycle (recreate via
`bootstrap-cycle --force-replace`). Logs:

```
{"event":"filter_skipped_empty_batch","chapter_id":N}
{"event":"state_transition","from":"FILTERING","to":"VOTACION"}
```

Total duration ≤ 100 ms (NFR target).

---

## 7. Admin replay endpoint (direct curl)

```sh
CHAPTER_UUID=$(docker exec -i $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist -tA -c "SELECT public_id FROM chapters WHERE status='live';")

curl -sS -X POST http://localhost:8000/api/v1/internal/director/replay \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"chapter_id\":\"$CHAPTER_UUID\"}" | jq
# {"classified": 3, "breakdown": {"approved":1, "rejected_offensive":1, "rejected_incoherent":1, "rejected_spam":0}}
```

Verify cycle state unchanged:

```sh
curl -sS http://localhost:8000/api/v1/internal/health/cycle | jq '.cycle.state'
# Whatever it was before; should not be touched by replay.
```

---

## 8. Live smoke against real Gemini (one batch)

If you want to verify the real LLM works without running the full filter:

```sh
uv run pytest -m live tests/live/test_gemini_smoke.py -v
```

Asserts a 3-twist batch returns a `DirectorBatchResponse` with 3 verdicts.

---

## 9. Prompt hash audit

After any edit to `prompts/*.j2` or `prompts/*.txt`:

```sh
uv run pytest tests/unit/test_director_prompts.py::test_prompt_hashes_match
# FAIL on mismatch — bump the constant in director_prompts.py.
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `filter_completed` shows `default_denied > 0` | LLM omitted twists | Inspect `llm_batch` log; if recurring, file an issue with the input batch |
| `slur_override_applied` storm | Test data triggered the slur list | Use placeholder content in dev |
| 429 from Gemini despite low traffic | Shared free-tier with another project | Check Google AI Studio quota dashboard |
| `LLMProviderInvalidOutput` repeatedly | Gemini model changed behavior | Pin model version in env; consider bumping prompt version |
| `replay-filter` CLI: "ADMIN_TOKEN not set" | Missing env | Set it in `.env.local` matching the Fly secret |
| Filter completes but cycle stuck in FILTERING | Transition failed (lock_busy?) | Inspect logs for `state_transition` event; retry via `replay-tick --to VOTACION --no-dwell-check` |
