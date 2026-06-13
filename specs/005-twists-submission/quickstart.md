# Quickstart: Twist Submission

**Branch**: `005-twists-submission` | **Date**: 2026-06-07 (updated 2026-06-13)
**Depends on**: modules 001 + 002 + 003 + 004 merged. Cycle in `RECEPCION_IDEAS`,
user redeemed an invite, JWT in hand.

## Status (2026-06-13)

| Phase | Status |
|---|---|
| 0 — migration 0007 | ✅ applied locally + ✅ applied to Neon (via Fly SSH) |
| 1 — pure domain (twist_content, twist_quota) | ✅ done + tests green |
| 2 — TwistsRepo + TwistLockBusy | ✅ done + integration tests green |
| 3 — TwistSubmissionService (submit + delete + list_mine) | ✅ done + integration tests green |
| 4 — HTTP endpoints (POST, DELETE, GET /me/twists) | ✅ code + integration tests green + **deployed to Fly (image `01KV0XYVE41F0DRJRFSCSTNET0`)** |
| 5 — race test | ✅ 2 tests green (10×concurrent → 3×201 + 7×409) |
| 6 — PWA (twist-api, twist-store, TwistModal, MyTwistsPanel, today.svelte) | ✅ done + vitest green; **Pages deploy pending** |
| 7 — SDD §5.5 patch + ADR-0002 | ✅ done |
| 7 — T-016 deploy + smoke | ✅ **done 2026-06-13**: Fly image deployed; 8/8 smoke asserts green vs prod (see "Verified prod smoke" block below) |

## Verified prod smoke (2026-06-13)

Run on cycle 2 (`cycle_date=2026-06-13`, state `RECEPCION_IDEAS`) after the
T-008/T-009/`tick-1201-recepcion` fixes plus the `_Q1_ACTIVE_TODAY` ordering
patch (`fix(004) commit 07d1266`). Invite-code `3LSA-Z2AL` issued via
`fly ssh console` to Neon; user `SmokeT016` redeemed the JWT against
`https://ai-plot-twist.fly.dev`. Eight asserts:

| # | Endpoint | Expected | Result |
|---|---|---|---|
| 1 | `POST /twists/submit` (happy) | 201, `pending_review`, quota-aware `remaining_submissions` | ✅ 201 |
| 2 | replay same `Idempotency-Key` + same body | 200 + same `public_id` | ✅ 200, same id |
| 3 | replay same `Idempotency-Key` + DIFFERENT body | 409 `idempotency_conflict` | ✅ 409 |
| 4 | submit until quota full | 201 × 2 | ✅ (observed via 3 inserts across attempts) |
| 5 | submit beyond quota | 409 `over_quota` (`quota_used=3, quota_max=3`) | ✅ 409 |
| 6 | `GET /me/twists` | 3 items + `quota={used:3,max:3,remaining:0}` | ✅ |
| 7 | `DELETE /twists/{id}` twice | 200 × 2, `deleted_at` stable across calls | ✅ stable `2026-06-13T17:11:32.831086+00:00` |
| 8 | submit after delete | 409 `over_quota` — delete does NOT free the slot (FR-004) | ✅ |

Smoke driver: `scripts/smoke_t016.py` (urllib + Python stdlib, no extra deps).

---

## 1. Apply the new migration

```sh
pnpm migrate
# alembic upgrade head → ... → 0007
```

Verify:

```sh
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist -c "\d twists"
```

---

## 2. Setup: get into RECEPCION_IDEAS with a JWT

```sh
# Bootstrap if not already done:
pnpm bootstrap-cycle --season s01-el-tunel --day-zero-manifest seed/cap0.yaml

# Force the cycle into RECEPCION_IDEAS
pnpm replay-tick --to ESTRENO
sleep 2
pnpm replay-tick --to RECEPCION_IDEAS

# Issue an invite and redeem it
INVITE=$(pnpm issue-invite --display-name-hint Lucia --count 1 --note dev | grep -oE '[A-Z2-7]{4}-[A-Z2-7]{4}')
RESP=$(curl -sS -X POST http://localhost:8000/api/v1/auth/redeem-invite \
  -H "Content-Type: application/json" \
  -d "{\"invite_code\":\"$INVITE\",\"display_name\":\"Lucia\"}")
JWT=$(echo "$RESP" | jq -r '.jwt')

# Get the live chapter's public_id
CHAPTER_ID=$(curl -sS http://localhost:8000/api/v1/chapters/today | jq -r '.chapter.id')
echo "JWT=$JWT"
echo "CHAPTER_ID=$CHAPTER_ID"
```

---

## 3. Submit a twist (happy path)

```sh
IDEM=$(uuidgen)
curl -sS -X POST http://localhost:8000/api/v1/twists/submit \
  -H "Authorization: Bearer $JWT" \
  -H "Idempotency-Key: $IDEM" \
  -H "Content-Type: application/json" \
  -d "{\"chapter_id\":\"$CHAPTER_ID\",\"content\":\"Mariana acepta hablar con su reflejo y este le confiesa que es del año 1998.\"}" | jq
```

Expected:

```json
{
  "twist": {
    "public_id": "b1c2…-uuid",
    "content": "Mariana acepta hablar con su reflejo…",
    "status": "pending_review",
    "submitted_at": "2026-06-08T16:42:11Z"
  },
  "remaining_submissions": 2
}
```

---

## 4. Idempotency replay

Run the same curl as step 3 with the SAME `IDEM`. Response: HTTP 200 (not 201) with
identical body.

```sh
curl -sS -X POST http://localhost:8000/api/v1/twists/submit \
  -H "Authorization: Bearer $JWT" \
  -H "Idempotency-Key: $IDEM" \
  -H "Content-Type: application/json" \
  -d "{\"chapter_id\":\"$CHAPTER_ID\",\"content\":\"Mariana acepta hablar con su reflejo y este le confiesa que es del año 1998.\"}" \
  -w "\nHTTP %{http_code}\n"
# HTTP 200
```

Tamper the body but reuse the key:

```sh
curl -sS -X POST http://localhost:8000/api/v1/twists/submit \
  -H "Authorization: Bearer $JWT" \
  -H "Idempotency-Key: $IDEM" \
  -H "Content-Type: application/json" \
  -d "{\"chapter_id\":\"$CHAPTER_ID\",\"content\":\"Algo distinto.\"}" \
  -w "\nHTTP %{http_code}\n"
# HTTP 409, code idempotency_conflict
```

---

## 5. Fill the quota

```sh
for i in 1 2 3 4; do
  curl -sS -X POST http://localhost:8000/api/v1/twists/submit \
    -H "Authorization: Bearer $JWT" \
    -H "Idempotency-Key: $(uuidgen)" \
    -H "Content-Type: application/json" \
    -d "{\"chapter_id\":\"$CHAPTER_ID\",\"content\":\"Idea número $i con suficiente texto para pasar la validación.\"}" \
    -w "\nHTTP %{http_code}\n"
done
# i=1,2,3 → 201
# i=4    → 409 over_quota
```

---

## 6. List own twists

```sh
curl -sS http://localhost:8000/api/v1/me/twists \
  -H "Authorization: Bearer $JWT" | jq
```

Expected: items array with 3 entries (all `status='pending_review'`), `quota:
{used:3, max:3, remaining:0}`.

---

## 7. Delete a twist (idempotent)

```sh
TWIST_ID=$(curl -sS http://localhost:8000/api/v1/me/twists \
  -H "Authorization: Bearer $JWT" | jq -r '.items[0].public_id')

curl -sS -X DELETE http://localhost:8000/api/v1/twists/$TWIST_ID \
  -H "Authorization: Bearer $JWT" | jq

# Re-delete (idempotent)
curl -sS -X DELETE http://localhost:8000/api/v1/twists/$TWIST_ID \
  -H "Authorization: Bearer $JWT" -w "\nHTTP %{http_code}\n"
# HTTP 200; deleted_at unchanged
```

Verify quota is NOT freed:

```sh
curl -sS -X POST http://localhost:8000/api/v1/twists/submit \
  -H "Authorization: Bearer $JWT" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d "{\"chapter_id\":\"$CHAPTER_ID\",\"content\":\"Intento de cuarta idea después de borrar.\"}" \
  -w "\nHTTP %{http_code}\n"
# HTTP 409 over_quota — delete did NOT free the slot
```

---

## 8. Cross-user deletion attempt

```sh
# Redeem a second user
INVITE2=$(pnpm issue-invite --display-name-hint Tomas --count 1 | grep -oE '[A-Z2-7]{4}-[A-Z2-7]{4}')
RESP2=$(curl -sS -X POST http://localhost:8000/api/v1/auth/redeem-invite \
  -H "Content-Type: application/json" \
  -d "{\"invite_code\":\"$INVITE2\",\"display_name\":\"Tomas\"}")
JWT2=$(echo "$RESP2" | jq -r '.jwt')

# Tomas tries to delete Lucia's twist
curl -sS -X DELETE http://localhost:8000/api/v1/twists/$TWIST_ID \
  -H "Authorization: Bearer $JWT2" \
  -w "\nHTTP %{http_code}\n"
# HTTP 403, code forbidden_not_owner
```

---

## 9. Submit outside the window

```sh
# Force-advance to FILTERING
pnpm replay-tick --to FILTERING --no-dwell-check

curl -sS -X POST http://localhost:8000/api/v1/twists/submit \
  -H "Authorization: Bearer $JWT" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d "{\"chapter_id\":\"$CHAPTER_ID\",\"content\":\"Tarde llegamos a la fiesta.\"}" \
  -w "\nHTTP %{http_code}\n"
# HTTP 409 window_closed
```

---

## 10. Race test (manual)

In two terminals, with the cycle in `RECEPCION_IDEAS` and the user at quota_used=2:

```sh
# Terminal A
curl -X POST http://localhost:8000/api/v1/twists/submit \
  -H "Authorization: Bearer $JWT" -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d "{\"chapter_id\":\"$CHAPTER_ID\",\"content\":\"Idea A simultánea con B.\"}" -w " %{http_code}\n" &

# Terminal B (same window)
curl -X POST http://localhost:8000/api/v1/twists/submit \
  -H "Authorization: Bearer $JWT" -H "Idempotency-Key: $(uuidgen)" \
  -H "Content-Type: application/json" \
  -d "{\"chapter_id\":\"$CHAPTER_ID\",\"content\":\"Idea B simultánea con A.\"}" -w " %{http_code}\n" &

wait
# Exactly one 201, the other 409 over_quota.
```

The automated `tests/integration/test_twist_submit_race.py` does this 10 times with
asyncio.gather and CI enforces.

---

## 11. PWA verification

Open `http://localhost:5173/today`. The page should show a "Tirá una idea" CTA
during `RECEPCION_IDEAS`. Click → modal appears with textarea + counter. Type ≥ 5
chars and hit "Enviar":

- Modal closes.
- "Mis ideas" panel appears with the new twist; it briefly shows "Enviando…" then
  resolves to `pending_review` chip.
- Quota indicator shows `2 restantes`.

Trigger the over-quota path (submit 3 then a 4th). The 4th should show a toast:
"Ya enviaste 3 ideas para este capítulo."

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| 422 on a content with only emojis | They might be `Cn` (unassigned) chars | Verify with `python -c "import unicodedata; print(unicodedata.category(...))"` |
| 503 lock_busy | Another submit holding the per-user lock | Should not happen except under chaos; retry the request |
| over_quota even though count seems < 3 | Soft-deleted twists count toward quota (FR-004) | This is by design |
| DELETE returns 409 already_filtered | The cycle advanced; the twist is no longer pending | Cannot recover; document for user |
| PWA shows "Enviando…" forever | The submit request never resolved | Check Network tab; the optimistic UI is waiting on the server |
