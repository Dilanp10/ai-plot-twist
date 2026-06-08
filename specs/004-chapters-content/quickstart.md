# Quickstart: Chapter Content Read API

**Branch**: `004-chapters-content` | **Date**: 2026-06-07
**Depends on**: modules 001 + 003 merged. Cycle bootstrapped via
`pnpm bootstrap-cycle`. API running on `:8000`.

---

## 1. Verify a chapter is live

```sh
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist \
  -c "SELECT day_index, status, released_at FROM chapters ORDER BY day_index;"
```

If no chapter has `status='live'`, fire the ESTRENO tick:

```sh
pnpm replay-tick --to ESTRENO
```

---

## 2. `GET /chapters/today`

```sh
curl -sS http://localhost:8000/api/v1/chapters/today | jq
```

Expected (truncated):

```json
{
  "cycle_state": "RECEPCION_IDEAS",
  "season": { "slug": "s01-el-tunel", "title": "El Túnel" },
  "chapter": {
    "id": "9f3a3b5f-...-7e2c",
    "day_index": 1,
    "title": "Lo que había detrás del espejo",
    "synopsis": "Mariana cruza el umbral y descubre que…",
    "released_at": "2026-06-08T15:00:00Z",
    "panels": [ { "idx": 1, "image_url": "https://assets.aiplottwist.example/...", "narration": "...", "mood": "tense" } ],
    "cliffhanger": "Una voz —la suya— le respondió desde el otro lado."
  },
  "windows": {
    "submit_until": "2026-06-08T21:00:00Z",
    "vote_from":    "2026-06-08T21:00:00Z",
    "vote_until":   "2026-06-09T02:00:00Z",
    "next_release": "2026-06-09T15:00:00Z"
  }
}
```

Inspect cache headers:

```sh
curl -sSI http://localhost:8000/api/v1/chapters/today
# Cache-Control: public, max-age=60, stale-while-revalidate=600, must-revalidate
# ETag: "a1b2c3d4e5f60718"
# Content-Type: application/json
```

---

## 3. Exercise `If-None-Match` (304)

```sh
ETAG=$(curl -sSI http://localhost:8000/api/v1/chapters/today | awk '/^ETag:/ {gsub(/[\r"]/,"",$2); print $2}')
curl -sS -o /dev/null -w "%{http_code}\n" \
  -H "If-None-Match: \"$ETAG\"" \
  http://localhost:8000/api/v1/chapters/today
# → 304
```

Force the ETag to change by firing a transition, then retry — must be 200.

```sh
pnpm replay-tick --to FILTERING --no-dwell-check
curl -sS -o /dev/null -w "%{http_code}\n" \
  -H "If-None-Match: \"$ETAG\"" \
  http://localhost:8000/api/v1/chapters/today
# → 200 (cycle_state is now FILTERING, ETag differs)
```

---

## 4. `GET /chapters/{public_id}` (live)

```sh
PUBLIC_ID=$(docker exec -i $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist -tA -c "SELECT public_id FROM chapters WHERE status='live' LIMIT 1;")
curl -sS http://localhost:8000/api/v1/chapters/$PUBLIC_ID | jq
```

Try an unreleased chapter (status='ready'):

```sh
READY_ID=$(docker exec -i $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist -tA -c "SELECT public_id FROM chapters WHERE status='ready' LIMIT 1;")
curl -sS -o /dev/null -w "%{http_code}\n" \
  http://localhost:8000/api/v1/chapters/$READY_ID
# → 404 chapter_not_found
```

---

## 5. `GET /seasons/{slug}`

```sh
curl -sS http://localhost:8000/api/v1/seasons/s01-el-tunel | jq
```

Expected: includes `bible_public` (only allowlisted keys: `setting`, `tone`,
`characters`, `rules`). If the bible has a `secrets` key, verify it's NOT present:

```sh
curl -sS http://localhost:8000/api/v1/seasons/s01-el-tunel | jq '.season.bible_public | keys'
# → ["characters","rules","setting","tone"]   (no "secrets")
```

---

## 6. Kill-switch behavior

```sh
pnpm kill-switch --on --reason "ajuste de bible"
curl -sS -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/v1/chapters/today
# → 503
curl -sS http://localhost:8000/api/v1/chapters/today | jq
# → { "type":"about:blank", "title":"Under maintenance",
#     "status":503, "code":"under_maintenance",
#     "reason":"ajuste de bible", "retry_after_seconds":3600 }

curl -sSI http://localhost:8000/api/v1/chapters/today | grep -i 'cache-control'
# Cache-Control: no-store

pnpm kill-switch --off
curl -sS -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/v1/chapters/today
# → 200 (within 30 s, after the system_flags cache expires)
```

---

## 7. No-active-season scenario

Drop down to direct DB and toggle the active season:

```sh
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist \
  -c "UPDATE seasons SET is_active = FALSE WHERE slug='s01-el-tunel';"

curl -sS http://localhost:8000/api/v1/chapters/today | jq
# → 503 no_active_season

# restore
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist \
  -c "UPDATE seasons SET is_active = TRUE WHERE slug='s01-el-tunel';"
```

---

## 8. PWA verification

Open `http://localhost:5173`. After onboarding (module 002), the `/today` route now
renders the real chapter from the API. Verify:

- Panel images load directly from the R2 URL (Network tab → request to
  `assets.aiplottwist.example`, not localhost:8000).
- The current state badge ("Recepción de ideas" / "Votación" / etc.) matches
  `cycle_state`.
- A countdown shows time until the next window boundary.
- Switching the kill-switch on (in another terminal) and waiting ≤ 30 s makes the
  PWA show the maintenance banner on the next refresh.

---

## 9. Load test

```sh
k6 run scripts/k6/today_burst.js
```

`today_burst.js` ramps to 200 RPS for 60 s. Acceptance: p95 < 500 ms, 0 5xx.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/chapters/today` returns 404 `no_live_chapter` | Bootstrap ran but ESTRENO tick never fired | `pnpm replay-tick --to ESTRENO` |
| Images don't load in PWA | CORS on the R2 bucket | Configure R2 bucket CORS to allow the Pages origin |
| ETag never matches | Server time skew vs `released_at` | Restart API; verify `released_at` is set in DB |
| 503 immediately after `pnpm kill-switch --off` | In-process cache still holds `on=true` | Wait 30 s OR restart API |
| Window timestamps look 3 h off | TZ bug: server computed in non-UTC | Inspect `cycle.state_entered_at` in DB; it should be `TIMESTAMPTZ` |
| PWA cache stale forever | Service worker not honoring `must-revalidate` | Hard-reload, check workbox config |
