# Quickstart: Auth via Invite Code

**Branch**: `002-auth-invite-flow` | **Date**: 2026-06-07
**Depends on**: module 001 quickstart complete (DB up, API reachable on `:8000`).

End-to-end walkthrough of the closed-beta auth flow: PO issues a code, user redeems it,
the PWA stores credentials, calls `/auth/me`, exercises refresh.

---

## 1. Apply the new migrations

```sh
pnpm migrate
# alembic upgrade head → 0001 → 0002 → 0003
```

Verify the three new tables exist:

```sh
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist -c "\dt"
# Expect: idempotency_keys, invites, users, rate_limit_buckets
```

---

## 2. Set `JWT_SECRET` in `.env.local`

If not already set in module 001:

```ini
# .env.local
JWT_SECRET=$(openssl rand -base64 32)   # or any 32+ bytes
```

Restart the API (the dev process picks up `.env.local` on reload).

---

## 3. PO issues an invite code

```sh
pnpm issue-invite --display-name-hint "Lucía" --ttl-days 7 --note "amiga"
```

Expected output (example):

```
Issued 1 invite.

  code        K7M3-PQ2X
  expires_at  2026-06-14T12:00:00-03:00
  note        amiga
  hint        Lucía

Send this code to the user. The hint is local-only (not sent on redemption).
```

Verify in DB:

```sh
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist \
  -c "SELECT code, status, expires_at, note FROM invites;"
```

To issue 5 at once:

```sh
pnpm issue-invite --count 5 --ttl-days 7
```

---

## 4. User redeems via PWA

Open `http://localhost:5173/onboarding`. Enter:

- Code: `K7M3-PQ2X` (auto-formatted as you type; lowercase accepted).
- Display name: `Lucía`.

Click "Empezar". Expected:

- Redirect to `/today` placeholder.
- DevTools → Application → IndexedDB → `aiplottwist` → `auth` shows two keys:
  - `jwt` → an HS256 token.
  - `device_secret` → base64url string ≈ 43 chars.

Verify against the API:

```sh
# Grab the JWT from IndexedDB → paste below
JWT="eyJhbGciOi..."
curl -sS http://localhost:8000/api/v1/auth/me \
  -H "Authorization: Bearer $JWT" | jq
```

Expected:

```json
{
  "user": {
    "public_id": "9f3a3b5f-...-7e2c",
    "display_name": "Lucía",
    "created_at": "2026-06-08T13:42:00Z",
    "last_seen_at": "2026-06-08T13:42:05Z"
  }
}
```

---

## 5. Equivalent redemption with curl (no PWA)

For backend-only testing:

```sh
curl -sS -X POST http://localhost:8000/api/v1/auth/redeem-invite \
  -H "Content-Type: application/json" \
  -d '{"invite_code":"K7M3-PQ2X","display_name":"Lucía"}' | jq
```

Expected response includes `jwt`, `device_secret`, `user`, `jwt_expires_at`.

---

## 6. Test refresh

```sh
SECRET="aZ6n..."   # device_secret from above
curl -sS -X POST http://localhost:8000/api/v1/auth/refresh \
  -H "Content-Type: application/json" \
  -d "{\"device_secret\":\"$SECRET\"}" | jq
# → {"jwt":"...new...","jwt_expires_at":"2026-09-06T..."}
```

The new JWT has a fresh `exp` 90 days out.

---

## 7. Test the error paths

```sh
# Bad code (typo / check digit fails)
curl -sS -X POST http://localhost:8000/api/v1/auth/redeem-invite \
  -H "Content-Type: application/json" \
  -d '{"invite_code":"AAAA-AAAA","display_name":"X"}'
# → 404 invite_not_redeemable

# Re-redeem already-used code
curl -sS -X POST http://localhost:8000/api/v1/auth/redeem-invite \
  -H "Content-Type: application/json" \
  -d '{"invite_code":"K7M3-PQ2X","display_name":"Otra"}'
# → 404 invite_not_redeemable  (collapsed for security)

# Bad display_name (too short)
curl -sS -X POST http://localhost:8000/api/v1/auth/redeem-invite \
  -H "Content-Type: application/json" \
  -d '{"invite_code":"NEXT-CODE","display_name":"X"}'
# → 422

# Rate limit (run 6 times in a row, the 6th returns 429)
for i in 1 2 3 4 5 6; do
  curl -sS -o /dev/null -w "%{http_code}\n" -X POST \
    http://localhost:8000/api/v1/auth/redeem-invite \
    -H "Content-Type: application/json" \
    -d '{"invite_code":"AAAA-AAAA","display_name":"X"}'
done
# → 404 404 404 404 404 429
```

---

## 8. Revoke and list

```sh
pnpm revoke-invite --code K7M3-PQ2X
# → "K7M3-PQ2X: cannot revoke — already redeemed"   (already used)

pnpm issue-invite --note "test"
# → ABCD-2345

pnpm revoke-invite --code ABCD-2345
# → "ABCD-2345: revoked"

pnpm list-invites
# code        status     expires_at                note
# K7M3-PQ2X   redeemed   2026-06-14T12:00:00-03:00 amiga
# ABCD-2345   revoked    2026-06-14T12:01:30-03:00 test
```

---

## 9. JWT expiration drill

Force-expire your JWT for testing:

```sh
# Re-mint with a 30-second exp via a one-shot Python (dev only)
uv run python -c "
import jwt, time, uuid
from app.settings import settings
print(jwt.encode({
  'sub': '9f3a3b5f-...-7e2c',
  'iss': 'ai-plot-twist', 'aud': 'web',
  'iat': int(time.time()), 'exp': int(time.time()) + 30,
  'jti': str(uuid.uuid4()),
}, settings.JWT_SECRET, algorithm='HS256'))
"
```

Wait 35 s, call `/auth/me` with that JWT — expect `401`. The PWA's fetch interceptor
should auto-call `/auth/refresh` and replay; verify in Network tab.

---

## 10. Banned-user behavior

Mark Lucía as banned (until the admin module ships, do it directly):

```sh
docker exec -it $(docker ps -qf "name=postgres") \
  psql -U app -d aiplottwist \
  -c "UPDATE users SET is_banned = TRUE WHERE display_name = 'Lucía';"
```

Wait up to 60 s (in-process LRU TTL) or restart the API. Then:

```sh
curl -sS http://localhost:8000/api/v1/auth/me \
  -H "Authorization: Bearer $JWT" -w "\n%{http_code}\n"
# → 403 banned
```

The PWA on receiving 403 with `code: banned` clears IndexedDB and routes to a "Te
fuiste del juego" page.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `pnpm issue-invite` fails with `ENV is 'prod'` | Safety guard | Run with `--allow-prod` only if you really mean it |
| `/auth/redeem-invite` returns 422 with `display_name_invalid` | Control char / RTL override stripped to < 2 chars | Use plain ASCII or common Spanish glyphs |
| `/auth/refresh` always 401 | `device_secret` doesn't match stored hash | Re-redeem (existing user is orphaned) |
| PWA infinite refresh loop | `/auth/refresh` also 401 | PWA logic broken; check the interceptor's "once" guard |
| Test on Windows: PowerShell can't run heredoc | Use the explicit PowerShell snippets in section 4 of module 001's quickstart for HMAC analog |
| `jwt.decode` raises `InvalidAudienceError` | `aud` claim mismatch | Verify env has `JWT_AUD=web` (or align with prod) |
