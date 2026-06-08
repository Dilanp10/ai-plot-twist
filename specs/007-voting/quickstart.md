# Quickstart: Voting

**Branch**: `007-voting` | **Date**: 2026-06-07
**Depends on**: modules 001 + 002 + 003 + 005 + 006 merged. API running.

---

## 1. Apply the new migration

```sh
pnpm migrate
# alembic upgrade head → ... → 0008
```

Verify:

```sh
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist -c "\d votes"
```

---

## 2. Setup: cycle in VOTACION with approved twists

Use the previous modules' quickstarts to:

1. Bootstrap a season + cycle.
2. Onboard a user via `/auth/redeem-invite`.
3. Submit 3 twists in `RECEPCION_IDEAS`.
4. Force `FILTERING` and let module 006 classify them.
5. Force the cycle into `VOTACION`.

```sh
pnpm replay-tick --to GENERACION --no-dwell-check
```

Wait — actually we want VOTACION, not GENERACION. Let's reach it cleanly:

```sh
pnpm replay-tick --to ESTRENO
pnpm replay-tick --to RECEPCION_IDEAS
# (submit twists)
pnpm replay-tick --to FILTERING --no-dwell-check
# (filter runs)
# cycle is now in VOTACION
```

Verify:

```sh
curl -sS http://localhost:8000/api/v1/internal/health/cycle | jq '.cycle.state'
# "VOTACION"
```

Confirm approved twists exist:

```sh
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist \
  -c "SELECT public_id, status, LEFT(content,40) FROM twists WHERE chapter_id=(SELECT id FROM chapters WHERE status='live') AND status='approved';"
```

---

## 3. Onboard a second user (so we have someone to vote)

```sh
INVITE=$(pnpm issue-invite --display-name-hint Voter --count 1 | grep -oE '[A-Z2-7]{4}-[A-Z2-7]{4}')
RESP=$(curl -sS -X POST http://localhost:8000/api/v1/auth/redeem-invite \
  -H "Content-Type: application/json" \
  -d "{\"invite_code\":\"$INVITE\",\"display_name\":\"Voter\"}")
JWT=$(echo "$RESP" | jq -r '.jwt')
```

---

## 4. Fetch the vote-feed

```sh
curl -sS "http://localhost:8000/api/v1/twists/vote-feed?limit=10" \
  -H "Authorization: Bearer $JWT" | jq
```

Expected:

```json
{
  "items": [
    { "id": "b1c2…", "content": "…", "vote_count": 0, "has_my_vote": false },
    ...
  ],
  "page": { "next_cursor": null, "limit": 10, "total_approved": 3 },
  "user_quota": { "used": 0, "max": 5, "remaining": 5 }
}
```

Refresh the call — items appear in the **same order** (stable seed):

```sh
curl -sS "http://localhost:8000/api/v1/twists/vote-feed?limit=10" \
  -H "Authorization: Bearer $JWT" | jq '.items[].id'
# same UUIDs in the same order
```

---

## 5. Cast a vote

```sh
TWIST_ID=$(curl -sS "http://localhost:8000/api/v1/twists/vote-feed?limit=1" \
  -H "Authorization: Bearer $JWT" | jq -r '.items[0].id')

curl -sS -X POST http://localhost:8000/api/v1/twists/vote \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d "{\"twist_id\":\"$TWIST_ID\"}" | jq
```

Expected:

```json
{
  "twist_id": "b1c2…",
  "new_vote_count": 1,
  "user_quota": { "used": 1, "max": 5, "remaining": 4 }
}
```

Re-call vote-feed:

```sh
curl -sS "http://localhost:8000/api/v1/twists/vote-feed" \
  -H "Authorization: Bearer $JWT" | jq '.items[] | select(.id=="'"$TWIST_ID"'") | .has_my_vote'
# true
```

---

## 6. Double-vote attempt

```sh
curl -sS -X POST http://localhost:8000/api/v1/twists/vote \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d "{\"twist_id\":\"$TWIST_ID\"}" -w "\nHTTP %{http_code}\n"
# HTTP 409 already_voted
```

---

## 7. Quota exhaustion

Vote on twists 2 through 5 (assuming you have ≥ 5 approved twists; if not, submit
more in module 005's flow):

```sh
for ID in $(curl -sS "http://localhost:8000/api/v1/twists/vote-feed?limit=10" \
  -H "Authorization: Bearer $JWT" | jq -r '.items[1:6][].id'); do
  curl -sS -X POST http://localhost:8000/api/v1/twists/vote \
    -H "Authorization: Bearer $JWT" \
    -H "Content-Type: application/json" \
    -d "{\"twist_id\":\"$ID\"}" -w " HTTP %{http_code}\n"
done
```

After 5 votes total: a 6th attempt returns 409 `over_quota`.

---

## 8. Sort modes

```sh
# Recent (newest first)
curl -sS "http://localhost:8000/api/v1/twists/vote-feed?sort=recent" \
  -H "Authorization: Bearer $JWT" | jq '.items[].content' | head -3

# Hot (most-voted first)
curl -sS "http://localhost:8000/api/v1/twists/vote-feed?sort=hot" \
  -H "Authorization: Bearer $JWT" | jq '.items | [.[] | {content, vote_count}]'
```

---

## 9. Cursor pagination

```sh
# Page 1
PAGE1=$(curl -sS "http://localhost:8000/api/v1/twists/vote-feed?sort=hot&limit=2" \
  -H "Authorization: Bearer $JWT")
CURSOR=$(echo "$PAGE1" | jq -r '.page.next_cursor')

# Page 2
curl -sS "http://localhost:8000/api/v1/twists/vote-feed?sort=hot&limit=2&cursor=$CURSOR" \
  -H "Authorization: Bearer $JWT" | jq
```

Cursor mismatch test:

```sh
curl -sS "http://localhost:8000/api/v1/twists/vote-feed?sort=random&cursor=$CURSOR" \
  -H "Authorization: Bearer $JWT" -w "\nHTTP %{http_code}\n"
# HTTP 422 cursor_invalid
```

---

## 10. Window enforcement

```sh
# Force-advance to GENERACION
pnpm replay-tick --to GENERACION --no-dwell-check

curl -sS "http://localhost:8000/api/v1/twists/vote-feed" \
  -H "Authorization: Bearer $JWT" -w "\nHTTP %{http_code}\n"
# HTTP 409 window_closed
```

---

## 11. Race test (manual)

Two terminals, one twist, same user:

```sh
# Terminal A
curl -X POST http://localhost:8000/api/v1/twists/vote \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d "{\"twist_id\":\"$TWIST_ID\"}" -w " %{http_code}\n" &
# Terminal B (same window)
curl -X POST http://localhost:8000/api/v1/twists/vote \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
  -d "{\"twist_id\":\"$TWIST_ID\"}" -w " %{http_code}\n" &
wait
# Exactly one 200, the other 409 already_voted. DB has 1 votes row for this user+twist.
```

`tests/integration/test_vote_race_same_twist.py` runs the same scenario at 10×
concurrency.

---

## 12. PWA verification

Open `http://localhost:5173`. The `/vote` route should activate when
`cycle_state == 'VOTACION'`. UI:

- Card-or-list view of approved twists.
- "👍" button on each.
- Optimistic count update + checkmark on tap.
- 5 dots indicator filling as user votes.
- Toast on `over_quota`: "Ya usaste tus 5 votos".
- Toast on `already_voted`: "Ya votaste esta idea".
- Disabled state on the "👍" button once `quota.remaining === 0`.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| 409 `twist_not_votable` on a twist that exists | Twist not `approved` or live chapter mismatch | Check `twists.status` |
| Feed returns 0 items but DB shows approved twists | Cycle is not the active cycle | Check `seasons.is_active` |
| Cursor decode fails (`cursor_invalid`) | Cursor from a previous deploy | Start fresh, ignore old cursor |
| Optimistic UI shows wrong count after refresh | Race with concurrent voter | Refresh re-syncs |
| Random sort changes between requests | `cycle_id` or `user_id` changed | Should not happen; restart and inspect logs |
