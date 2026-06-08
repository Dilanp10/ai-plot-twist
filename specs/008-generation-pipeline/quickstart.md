# Quickstart: Generation Pipeline

**Branch**: `008-generation-pipeline` | **Date**: 2026-06-07
**Depends on**: modules 001 + 002 + 003 + 005 + 006 + 007 + 009 merged.

This module closes the loop: filter → vote → **generate** → release → … Once
this ships, the system runs end-to-end autonomously.

---

## 1. Set R2 credentials and upload static assets

```sh
# .env.local
R2_ACCOUNT_ID=<your-cloudflare-r2-account>
R2_ACCESS_KEY_ID=<r2-api-key>
R2_SECRET_ACCESS_KEY=<r2-secret>
R2_BUCKET=aiplottwist-assets-dev
R2_PUBLIC_BASE_URL=https://assets-dev.aiplottwist.example
PLACEHOLDER_IMAGE_URL=https://assets-dev.aiplottwist.example/static/placeholder.webp

SCRIPTWRITER_TEMPERATURE=0.6
SCRIPTWRITER_MAX_OUTPUT_TOKENS=4096
PANEL_CONCURRENCY=4
PIPELINE_HARD_DEADLINE_S=3300

TTS_ENABLED=true
TTS_VOICE=es-AR-ElenaNeural
```

Upload the placeholder once:

```sh
pnpm --filter ./apps/api run upload-static-assets
# uploads assets/placeholder.webp → R2 at static/placeholder.webp
```

Verify:

```sh
curl -sSI $PLACEHOLDER_IMAGE_URL | head -3
# HTTP/2 200, Content-Type: image/webp
```

---

## 2. Walk the cycle into GENERACION with a real winner

Using prior modules' quickstarts: bootstrap, onboard ≥ 2 users, submit twists,
run filter (006), cast votes (007).

```sh
# Force to VOTACION via the FSM
pnpm replay-tick --to ESTRENO
pnpm replay-tick --to RECEPCION_IDEAS
# (submit twists via the PWA or curl from module 005's quickstart)
pnpm replay-tick --to FILTERING --no-dwell-check
# (vote via the PWA or curl from module 007's quickstart)
```

Check DB state before generation:

```sh
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist -c "
SELECT t.public_id, t.status, COUNT(v.id) AS votes
  FROM twists t LEFT JOIN votes v ON v.twist_id = t.id
 WHERE t.chapter_id = (SELECT id FROM chapters WHERE status='live')
   AND t.status = 'approved'
 GROUP BY t.id;
"
```

---

## 3. Fire the 23:00 transition

```sh
pnpm replay-tick --to GENERACION --no-dwell-check
```

Watch the API log. Expected event stream (truncated):

```
state_transition from=VOTACION to=GENERACION
side_effect_started name=generation_pipeline chapter_id=1
generation_started chapter_id=1 has_winner=true twist_count=5
winner_picked twist_id=b1c2… vote_count=12 tiebreak=false
scriptwriter_done provider=gemini model=gemini-2.0-flash latency_ms=4321 panels=3
panel_render_started panel_idx=1 seed=8472
panel_render_started panel_idx=2 seed=193
panel_render_started panel_idx=3 seed=51
image_provider_attempt provider=pollinations panel=1 outcome=success
image_provider_attempt provider=pollinations panel=2 outcome=success
image_provider_attempt provider=pollinations panel=3 outcome=success
panel_render_done panel_idx=1 provider=pollinations latency_ms=8421 ok=true
panel_render_done panel_idx=2 provider=pollinations latency_ms=9012 ok=true
panel_render_done panel_idx=3 provider=pollinations latency_ms=7901 ok=true
tts_done panel_idx=1 ok=true latency_ms=2104
tts_done panel_idx=2 ok=true latency_ms=2210
tts_done panel_idx=3 ok=true latency_ms=2055
r2_upload_done key=seasons/s01-el-tunel/<uuid>/1-aa…webp ok=true
... (5 more uploads)
generation_completed chapter_id=2 status=ready duration_ms=37250
state_transition from=GENERACION to=PENDING_RELEASE
```

Verify DB:

```sh
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist -c "
SELECT day_index, status, title FROM chapters ORDER BY day_index DESC LIMIT 3;
SELECT id, state, next_chapter_id FROM cycles WHERE season_id=(SELECT id FROM seasons WHERE is_active);
"
```

Expect a new chapter row, status='ready'; cycle in `PENDING_RELEASE` with
`next_chapter_id` set.

---

## 4. Verify the assets

```sh
NEW_CHAPTER=$(docker exec -i $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist -tA -c "
SELECT manifest_json->'panels'->0->>'image_url' FROM chapters ORDER BY day_index DESC LIMIT 1;
")
curl -sSI "$NEW_CHAPTER" | head -3
# HTTP/2 200, Content-Type: image/webp
```

Open in a browser to inspect.

---

## 5. Trigger ESTRENO of the new chapter

```sh
pnpm replay-tick --to ESTRENO
curl -sS http://localhost:8000/api/v1/chapters/today | jq '{chapter:{id, day_index, title}, cycle_state}'
# day_index incremented; cycle_state=ESTRENO
```

You've just closed the loop end-to-end.

---

## 6. Test the no-winner path

Wipe the votes table for the live chapter and re-run:

```sh
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist -c "
DELETE FROM votes WHERE chapter_id = (SELECT id FROM chapters WHERE status='live');
"
# Move all twists to rejected_* so winner-selection returns 0 rows:
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist -c "
UPDATE twists SET status='rejected_incoherent' WHERE chapter_id = (SELECT id FROM chapters WHERE status='live');
"

pnpm replay-tick --to GENERACION --no-dwell-check
```

Logs should show:

```
winner_picked twist_id=None vote_count=0 ...
cycle_autocontinued chapter_id=...
scriptwriter_done ... (uses scriptwriter_v1_auto.system.txt)
generation_completed ... status=ready (NOT ready_degraded)
```

---

## 7. Test partial panel failure

Inject a failing panel by tampering with the visual_prompt to be unparseable
(or block the Pollinations DNS as in module 009's quickstart). Then run the
pipeline.

Expected: 1+ panels fall back to placeholder URL; chapter status =
`ready_degraded`; Discord webhook fires with the failure summary.

---

## 8. Test deadline

```sh
# Drop the deadline very low for a smoke
PIPELINE_HARD_DEADLINE_S=10 pnpm dev    # restart API with override

# Rerun generation; expect deadline_exceeded log + ready_degraded
pnpm rerun-generation --chapter-id <uuid-of-current-next>
```

Restore default after.

---

## 9. Admin rerun (replace a bad chapter)

```sh
CHAPTER_UUID=$(docker exec -i $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist -tA -c "SELECT public_id FROM chapters WHERE status='ready' ORDER BY day_index DESC LIMIT 1;")

pnpm rerun-generation --chapter-id $CHAPTER_UUID
# Watches the full pipeline again; replaces manifest in place; bumps released_at.
```

Verify caches invalidate (module 004 ETag changes):

```sh
curl -sSI http://localhost:8000/api/v1/chapters/today | grep ETag
# Different from before
```

---

## 10. Live smoke (real providers + real R2)

```sh
uv run pytest -m live tests/live/test_full_pipeline_smoke.py -v
```

End-to-end against Gemini + Pollinations + R2 dev bucket. Manual / nightly only.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `generation_completed status=ready_degraded` immediately | Single panel failed | Inspect `degraded_reasons` in manifest; `rerun-generation` |
| Cycle stuck in `GENERACION` past 1 h | Pipeline crashed; safe_side_effect should have triggered | Check for FAILED state; replay-tick + rerun-generation |
| `Boto3 PutObject ConnectionError` repeatedly | R2 credentials invalid or region wrong | Verify `.env.local` against Cloudflare dashboard |
| `scriptwriter_done` but visual_prompt is Spanish | Prompt drift | Bump prompt hash + adjust system prompt explicit phrase |
| TTS missing on all panels | `TTS_ENABLED=false` or edge-tts upstream down | Set true; library failures auto-skip |
| New chapter's `released_at` is from before today | Rerun-generation not invoked; or pipeline finalized but didn't bump | Module 004 ETag may be stale until next live-flip |
| All panels use placeholder URL | `ImageProviderRouter` exhausted both providers | Check `image_provider_exhausted` log; verify HF token |
| `manifest_json.schema_version` missing | Old chapter from before this module | Backfill or ignore (module 004 reads required keys only) |
