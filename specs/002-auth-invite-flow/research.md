# Phase 0 Research: Auth via Invite Code + Device-Bound JWT

**Branch**: `002-auth-invite-flow` | **Date**: 2026-06-07

Mini-ADRs for each non-trivial decision in this module's plan.

---

## R-001 — JWT signing algorithm

**Question**: HS256, RS256, EdDSA, or Paseto?

| Option | Pros | Cons |
|---|---|---|
| **HS256 (chosen)** | Symmetric, single secret, fastest verification, smallest token | Same key signs and verifies → exposing it forges tokens; no JWKS rotation flow |
| RS256 | Public/private key, JWKS standard, third-party verification possible | More setup, longer tokens, slower verification |
| EdDSA (Ed25519) | Modern, fast, short keys | Less mature library support in some clients |
| Paseto v4 | Designed against JWT footguns | Smaller ecosystem; would force us off PyJWT |

**Decision**: **HS256**.

**Rationale**: there is only one verifier in the MVP architecture (the FastAPI app).
There is no third-party that needs to validate tokens. The asymmetric advantages of RS256
(JWKS, third-party verification) are wasted complexity here. PyJWT supports HS256 out of
the box, no extra deps. Key rotation is achieved by changing `JWT_SECRET` in Fly secrets —
this invalidates all live JWTs in flight, forcing a one-time refresh; acceptable in closed
beta.

**Trigger to revisit**: when the API is consumed by a third-party (e.g., a Telegram bot
adapter that wants to verify our JWTs offline) or when key rotation without forced
re-login becomes a requirement.

---

## R-002 — JWT lifetime + refresh model

**Question**: Short JWT + refresh-token rotation, or long JWT + device-bound refresh?

| Option | Pros | Cons |
|---|---|---|
| Short JWT (15 min) + refresh token (90 d) | Industry standard, smaller blast radius on JWT leak | Rotation logic, refresh token replay detection, more endpoints |
| **Long JWT (90 d) + `device_secret`-based refresh (chosen)** | Simplest implementation, no rotation bookkeeping; device_secret IS the long-lived credential | Larger blast radius if JWT leaks (90 d window) |
| Single ultra-long JWT, no refresh | Simplest of all | 90-day cliff forces re-invitation; UX killer |

**Decision**: **long JWT + device_secret refresh**.

**Rationale**: in closed beta, the realistic threat model is "user closes the tab and
returns 3 weeks later", not "attacker exfiltrates JWT and uses it for 89 days." The PO
can ban a user instantly. Operational simplicity wins. Refresh is a single endpoint
that takes the device_secret and returns a new JWT; the device_secret never changes.

**Trigger to revisit**: any incident where a JWT is found leaked. Reduce JWT lifetime
to 7 days and add a real refresh-token table.

---

## R-003 — Invite code format

**Question**: UUID, base32 short code, word triplet (BIP-39 style), or numeric?

| Option | Length | UX | Collision space | Comment |
|---|---|---|---|---|
| UUID | 36 chars | Awful to dictate | 122 bits | Wastes characters for a closed beta |
| Numeric 6-digit | 6 chars | Easy but predictable | 20 bits | Too small; brute-force feasible without rate limit |
| Word triplet (BIP-39) | varies | Easy to dictate, easy to mistype | ~33 bits per 3 words | Fun but pulls in a wordlist; mismatches in inflection |
| **base32 8-char w/ check digit (chosen)** | 9 chars (`XXXX-XXXX`) | Easy to type; dictateable; no 0/O/1/I/L confusion (RFC 4648 alphabet `A-Z2-7`) | 35 bits effective + 5-bit check digit | Compact, safe, self-validating |

**Decision**: **8-char base32 with last char as check digit.** Last char is
`base32(sha256(first 7 chars)[0:5])[0]`. Server can reject a typo without a DB lookup —
tightens the rate limit budget for real attackers.

**Format**: `XXXX-XXXX` with a hyphen for readability. Case-insensitive on input;
canonicalized to uppercase before storage.

**Trigger to revisit**: ≥ 1000 users (collision space gets tighter; we'd move to 10
chars).

---

## R-004 — Where the device secret lives on the client

**Question**: localStorage, sessionStorage, IndexedDB, HttpOnly cookie?

| Option | Pros | Cons |
|---|---|---|
| localStorage | Simple API | Cleared on "clear site data"; accessible to any XSS |
| sessionStorage | Same as localStorage but tab-scoped | Loses on tab close; bad UX |
| **IndexedDB (chosen)** | Survives most "clear cache" actions; structured storage; quotas | Async API; slightly more code than localStorage |
| HttpOnly cookie | XSS-immune | CSRF surface; doesn't play with `Authorization: Bearer` model; same-origin only |

**Decision**: **IndexedDB** under db name `aiplottwist`, object store `auth`, keys
`jwt` and `device_secret`. Both XSS-vulnerable like localStorage; IndexedDB is chosen
for resilience to user actions, not security. We accept the XSS risk: the PWA loads
only from its own origin, has CSP `script-src 'self'`, and the closed beta is small.

**Trigger to revisit**: when public launch is on the table, evaluate HttpOnly cookie
+ CSRF tokens as the production pattern.

---

## R-005 — Single-device vs multi-device per user

**Question**: Should one `users` row support multiple concurrent device sessions?

| Option | Pros | Cons |
|---|---|---|
| **Single-device (chosen for MVP)** | Simplest schema (one `device_token` per user), simplest flow | User cannot use phone + laptop concurrently; re-redeem on second device creates a second user |
| Multi-device | Better UX | Requires `user_devices` table with N rows per user; refresh logic disambiguates; bans cascade per-device or per-user |

**Decision**: **single-device** in MVP. Documented as OQ-AUTH-1 to be addressed in v0.2
if user feedback shows phone+desktop is a real need. Most closed-beta users will use
one phone.

**Workaround for the PO**: if a user demands a second device, the PO issues a fresh
invite code; the user creates a parallel account on the second device. Loss of unified
history is acceptable for MVP.

---

## R-006 — Rate-limit backend

**Question**: Redis (managed) vs in-memory vs Postgres-backed sliding window?

| Option | Pros | Cons |
|---|---|---|
| Redis Cloud free tier | Sub-ms latency, atomic ops | Yet another service; free tier is tiny |
| In-memory `dict` + lock | Free, fast | Lost on restart; doesn't survive Fly machine recycle |
| **PG `rate_limit_buckets` table with hourly buckets (chosen)** | Reuses the DB we already have; survives restarts; ACID | One DB row read + upsert per redemption; acceptable for low-RPS endpoint |

**Decision**: PG-backed. One `INSERT … ON CONFLICT DO UPDATE SET count = count + 1`
per request. Bucket key = `f"redeem:ip:{client_ip}"`, `window_start = date_trunc('hour',
now())`. Cleanup job (added with module 003 watchdog) deletes rows older than 7 days.

**Trigger to revisit**: if any rate-limited endpoint sustains > 100 RPS, move to Redis.

---

## R-007 — Ban enforcement strategy

**Question**: Check `is_banned` in DB on every request, or embed in JWT?

| Option | Latency | Ban-effective-after | Cache |
|---|---|---|---|
| Embed in JWT | 0 ms | Up to 90 d (until token expires) | N/A |
| **DB lookup per request (chosen)** | +5–10 ms | Immediate | 60 s in-process LRU |
| Maintain a blocklist of `jti` | +5 ms | Immediate | Requires `jti` storage |

**Decision**: **DB lookup with 60 s in-process LRU cache** keyed by `user.public_id`.
Bans take effect within 60 s. Cache size capped at 1024 entries (closed beta has ≤ 100
users; cap is generous). The cache is local per Fly machine; multi-machine deploys would
need pub/sub invalidation — N/A in MVP single-machine.

---

## R-008 — Display name uniqueness

**Question**: Should display names be unique?

**Decision**: **No.** Two users can share a display name. Disambiguation in the UI is by
`public_id` short prefix or initials avatar. Forcing uniqueness creates a poor onboarding
moment ("name already taken, try Lucía123") that is hostile to the family-friends vibe.

---

## R-009 — CSRF

**Question**: Do we need CSRF protection?

**Decision**: **No, not in this module.** The PWA sends `Authorization: Bearer <jwt>`
explicitly via fetch, not via cookies. A cross-origin attacker page cannot read the
JWT from IndexedDB (same-origin policy). The CSRF threat surface is therefore minimal.

When module 010 ships the PWA at its real domain, we'll set CORS allow-list to that
origin only — closes the residual surface.

**Trigger to revisit**: if any endpoint is ever moved to cookie-based auth.

---

## R-010 — `python-ulid` vs `uuid` for `jti`

**Question**: What format should the JWT `jti` claim use?

**Decision**: **ULID** via `python-ulid`. It's time-sortable (helpful for log
correlation) and 128-bit (same security as UUID v4). Minor extra dep, well worth it.

---

## R-011 — Idempotency on `/auth/redeem-invite`

**Question**: Should `Idempotency-Key` be required, optional, or absent?

**Decision**: **Optional but honored**. The PWA does not send one on first redemption
(no reason to retry), but if the PWA is built later with retry logic, the key prevents
a race that would double-create users. We persist results in `idempotency_keys` (table
from module 001) for 14 days.

---

## Open items (carried to follow-up modules)

- **OQ-AUTH-1**: Multi-device per user. Defer to a "profile" module if/when users ask.
- **OQ-AUTH-2**: JWKS / asymmetric JWT. Defer until a second verifier exists.
- **OQ-AUTH-3**: User-initiated logout. Currently the only logout is "delete IndexedDB"
  manually. Add `POST /auth/logout` that rotates the user's `device_token` server-side
  in a future module.
