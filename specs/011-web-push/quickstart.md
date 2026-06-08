# Quickstart: Web Push

**Branch**: `011-web-push` | **Date**: 2026-06-07
**Depends on**: modules 001–010 merged. Real device required for full smoke.

---

## 1. Generate VAPID keys

```sh
pnpm generate-vapid
```

Output:

```
VAPID keypair generated. Save these:

  VAPID_PUBLIC_KEY=BNc4...long-base64url...
  VAPID_PRIVATE_KEY=qFR3...long-base64url...

Set VAPID_SUBJECT to a contact URL (e.g. mailto:po@example.com).
```

Add to `.env.local`:

```ini
VAPID_PUBLIC_KEY=<from-output>
VAPID_PRIVATE_KEY=<from-output>
VAPID_SUBJECT=mailto:po@aiplottwist.example
PUSH_FANOUT_TIMEOUT_S=60
PUSH_FAILURE_THRESHOLD=3
```

For production: store as Fly secrets:

```sh
fly secrets set VAPID_PRIVATE_KEY="..." VAPID_SUBJECT="..."
# VAPID_PUBLIC_KEY can stay in plain env (it's public)
```

---

## 2. Apply the migration

```sh
pnpm migrate
# alembic upgrade head → 0009
```

Verify:

```sh
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist -c "\d push_subscriptions"
```

---

## 3. Verify public-key endpoint

```sh
curl -sS http://localhost:8000/api/v1/push/public-key | jq
# {"public_key": "BNc4..."}
```

The PWA fetches this at app boot.

---

## 4. PWA: opt in to notifications

Open `http://localhost:5173/settings`. Tap the "Notificaciones" toggle.

Expected:
- Browser permission prompt appears.
- On grant: toggle flips to ON, the PWA calls
  `navigator.serviceWorker.ready.then(reg => reg.pushManager.subscribe(...))`,
  POSTs the result to `/api/v1/push/subscribe`.
- DB:
  ```sh
  docker exec -it $(docker ps -qf "name=postgres") \
    psql -U app -d aiplottwist \
    -c "SELECT id, user_id, LEFT(endpoint, 60), failure_count FROM push_subscriptions;"
  # one row
  ```

---

## 5. Test push via admin endpoint

```sh
USER_UUID=$(docker exec -i $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist -tA -c "SELECT public_id FROM users ORDER BY id LIMIT 1;")

curl -sS -X POST http://localhost:8000/api/v1/internal/push/test \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"target_user_public_id\":\"$USER_UUID\"}" | jq
# {"sent": 1, "failed": 0, "gone": 0, "skipped_idempotent": false}
```

On the device with the PWA open: a notification should appear within seconds.

Tap the notification: the PWA should focus (or open) at `/today`.

---

## 6. Test fan-out on ESTRENO

Ensure the cycle is in `PENDING_RELEASE` with a `ready` next chapter
(from module 008's quickstart). Then fire the 12:00 transition:

```sh
pnpm replay-tick --to ESTRENO
```

Expected API logs:

```
state_transition from=PENDING_RELEASE to=ESTRENO
push_fanout_started chapter_id=2 total_subs=1
push_sent subscription_id=1
push_fanout_completed chapter_id=2 sent=1 gone=0 failed=0 duration_ms=812
```

The device receives the notification: "AI Plot Twist — Día 8" / "Hoy: <title>".

---

## 7. Test idempotency

```sh
# Re-run the same admin test without force:
curl -sS -X POST http://localhost:8000/api/v1/internal/push/test \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"target_user_public_id\":\"$USER_UUID\"}" | jq
# {"sent": 0, "failed": 0, "gone": 0, "skipped_idempotent": true}
```

Force a re-send for testing:

```sh
curl -sS -X POST "http://localhost:8000/api/v1/internal/push/test?force=true" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"target_user_public_id\":\"$USER_UUID\"}" | jq
```

---

## 8. Test 410 Gone cleanup

Force a Gone response by tampering:

```sh
# 1. Note the current subscription's id
SUB_ID=$(docker exec -i $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist -tA -c "SELECT id FROM push_subscriptions LIMIT 1;")

# 2. Corrupt the endpoint to a known-bad URL
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist \
  -c "UPDATE push_subscriptions SET endpoint='https://example.invalid/push/x' WHERE id=$SUB_ID;"

# 3. Trigger fan-out
curl -sS -X POST "http://localhost:8000/api/v1/internal/push/test?force=true" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"target_user_public_id\":\"$USER_UUID\"}" | jq
# Expect {gone: 1} or {failed: 1, ...} depending on the test endpoint's response

# 4. Verify cleanup
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist \
  -c "SELECT id, failure_count FROM push_subscriptions;"
# Row may be deleted (gone) or have failure_count > 0
```

Re-subscribe via the PWA to recover.

---

## 9. Test unsubscribe

In the PWA settings, toggle off "Notificaciones". Verify:

- Browser-side: `pushManager.getSubscription()` returns `null` afterward.
- DB: row deleted.

---

## 10. Real-device smoke (the bar)

The PO performs this on their own Android phone (and ideally iOS too) before
declaring the module done:

1. Install the PWA from production URL (per module 010's install quickstart).
2. Open the installed app.
3. Navigate to Settings → toggle Notifications ON.
4. Grant permission.
5. Close the app entirely (swipe away).
6. PO runs `pnpm rerun-generation --chapter-id <today's-next>`.
7. PO runs `pnpm replay-tick --to ESTRENO`.
8. Within 30 s: notification appears on the lock screen.
9. Tap it. The PWA opens at `/today` with the new chapter.

If this completes, module 011 is "done" and the closed-beta is shippable.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `vapid_not_configured` on `/push/public-key` | `VAPID_PRIVATE_KEY` not set | Set Fly secret and restart |
| Subscribe returns 401 | JWT expired | Refresh via `/auth/refresh` (handled by api.ts interceptor) |
| No notification on test | Browser permission denied | Check `Notification.permission` in DevTools console |
| Notification opens to wrong URL | SW `notificationclick` handler missing | Verify `service-worker.ts` includes the handler from FR-013 |
| iOS: nothing happens on toggle | iOS Safari needs PWA installed first | Install from Home Screen, then retry |
| `push_fanout_skipped_idempotent` on first ever ESTRENO | Stale `idempotency_keys` row | Manually DELETE that key for testing |
| Notification arrives but title is wrong | Payload composition bug | Inspect `push_payload.compose(...)` output in unit test |
| Multiple notifications for same chapter | Old browsers don't dedup by `tag` | Acceptable degradation; not a regression in modern browsers |
