# Feature Specification: Auth via Invite Code + Device-Bound JWT

**Feature Branch**: `002-auth-invite-flow`
**Created**: 2026-06-07
**Status**: Draft
**Depends on**: `001-project-bootstrap`

## Summary

Introduce the first authenticated identity in the system: a closed-beta user who joins
by redeeming a single-use **invite code** + choosing a **display name**. The server
binds the user to the device that redeemed (via a server-issued `device_secret`),
issues a long-lived JWT (HS256, 90 days), and exposes a `/auth/refresh` endpoint that
reissues a JWT from the device secret without re-redeeming the code. No email, no
password, no OAuth.

The Product Owner mints codes via a CLI (`pnpm issue-invite`); a separate CLI
(`pnpm revoke-invite`) and a status query (`pnpm list-invites`) let the PO manage the
small population. Anti-abuse: per-IP rate limit on redemption, server-side check digit
on the code, and constant-time secret comparison on refresh.

## User Scenarios & Testing

### User Story 1 — PO issues an invite code and onboards a friend (Priority: P1)

The Product Owner generates a code from their dev machine, sends it via WhatsApp, and
the friend redeems it inside the PWA in under 60 seconds.

**Why this priority**: this is the only way to grow the closed-beta population. Without
it, the product has zero users.

**Independent Test**: PO runs `pnpm issue-invite --display-name-hint "Lucía"`. Output
contains a code in the format `XXXX-XXXX`. PO sends it to Lucía. Lucía opens the PWA,
enters the code + her display name, and lands on a "logged-in" view that shows her
display name and a `/auth/me` response.

**Acceptance Scenarios**:

1. **Given** the PO is on a machine with the repo checked out and the DB reachable,
   **When** they run `pnpm issue-invite --display-name-hint "Lucía" --ttl-days 7
   --note "amiga"`,
   **Then** the script prints a code matching `^[A-Z2-7]{4}-[A-Z2-7]{4}$`, inserts a
   row in `invites` with `status='unused'` and `expires_at = now() + 7d`, and exits 0.

2. **Given** a fresh PWA install with the issued code,
   **When** the user submits `code = "K7M3-PQ2X"` and `display_name = "Lucía"` to
   `POST /api/v1/auth/redeem-invite`,
   **Then** the server creates a `users` row, marks the invite `redeemed`, returns
   HTTP 201 with a JSON body containing `jwt` (a valid HS256 token) and `device_secret`
   (a base64url 32-byte string), and the JWT decodes to claims
   `{sub: <user.public_id>, iss: "ai-plot-twist", aud: "web", exp: now+90d}`.

3. **Given** the user holds a valid JWT,
   **When** they call `GET /api/v1/auth/me` with `Authorization: Bearer <jwt>`,
   **Then** the response is HTTP 200 with body `{user: {public_id, display_name,
   created_at, last_seen_at}}` and `users.last_seen_at` is updated server-side.

### User Story 2 — JWT expires; client refreshes silently (Priority: P1)

After 90 days the JWT expires. The PWA detects the `401` on the first authenticated
call, transparently calls `/auth/refresh` with the persisted device secret, and replays
the original request — the user sees no interruption.

**Why this priority**: a 90-day cliff that forces re-invitation kills retention.

**Acceptance Scenarios**:

1. **Given** a user with a stored `device_secret` and an expired JWT,
   **When** the PWA calls `POST /api/v1/auth/refresh` with body `{device_secret: "..."}`,
   **Then** the server hashes the secret (SHA-256), looks up the matching `users.device_token`,
   verifies the user is not banned, issues a new JWT (HS256, exp = now + 90 d), and returns
   HTTP 200 with `{jwt}` (no new device_secret — the existing one stays valid).

2. **Given** an invalid `device_secret`,
   **When** the client calls `/auth/refresh`,
   **Then** the server replies HTTP 401 in a time indistinguishable (within ±5 ms p50) from
   a valid call followed by `is_banned=true`. No information leak on which check failed.

### User Story 3 — Code reuse is blocked (Priority: P1)

Each invite code is single-use. A second attempt — even by the legitimate first user
on a second device — fails with a clear error.

**Acceptance Scenarios**:

1. **Given** a code already redeemed,
   **When** any client posts it to `/auth/redeem-invite`,
   **Then** HTTP 409 with `{"code":"invite_already_redeemed"}` and no DB write.

2. **Given** a code that does not exist OR was revoked OR is expired,
   **When** any client posts it,
   **Then** HTTP 404 with `{"code":"invite_not_redeemable"}`. The three cases collapse
   into one error code to avoid leaking which codes exist.

### User Story 4 — Banned user is rejected (Priority: P2)

The PO bans a user (sets `is_banned=true`). The user's JWT continues to decode but
every authenticated endpoint returns 403.

**Acceptance Scenarios**:

1. **Given** `users.is_banned = TRUE` for user U,
   **When** U calls any authenticated endpoint with a still-valid JWT,
   **Then** HTTP 403 with `{"code":"banned"}` within 100 ms p95. The PWA logs out the
   user on receiving this code.

### User Story 5 — Brute-force redemption is blocked (Priority: P2)

An attacker tries codes en masse from a single IP. The server rate-limits to 5
attempts per IP per hour.

**Acceptance Scenarios**:

1. **Given** an IP has made 5 failed `/auth/redeem-invite` attempts in the last hour,
   **When** the same IP makes a 6th attempt,
   **Then** HTTP 429 with `Retry-After` header and `{"code":"too_many_redemptions"}`,
   regardless of whether the 6th code is valid.

### Edge Cases

- **Code with bad check digit**: rejected as `invite_not_redeemable` (404) without a
  DB lookup. Saves a round-trip and tightens the rate-limit budget for real attackers.
- **Code in lowercase or with spaces**: normalized server-side (`upper().replace(" ",
  "").replace("-", "")` then re-format) before lookup.
- **Display name with banned characters / Unicode tricks**: server normalizes
  (NFKC), strips control chars, trims, then validates length 2..24. Common bidi /
  RTL overrides rejected.
- **Two clients redeem the same code in the same millisecond**: PG row lock on
  `invites WHERE code = ? FOR UPDATE` serializes; one succeeds, the other gets 409.
- **Device secret leak**: documented out-of-scope for MVP. Mitigation: short-lived
  JWT (90 d) means a refresh token leak is bounded. PO can ban the affected user.
- **Clock skew between client and server on JWT exp**: server gives a 60 s grace
  window on `exp` for inbound JWTs (RFC 7519 §4.1.4 nbf/exp leeway).
- **PWA loses IndexedDB (browser data cleared)**: device_secret gone. User cannot
  refresh. Must request a new invite code. Documented in the help screen.
- **Future multi-device (out of scope MVP)**: documented as OQ-AUTH-1.

## Requirements

### Functional Requirements

- **FR-001**: The system MUST expose `POST /api/v1/auth/redeem-invite` accepting
  `{invite_code, display_name}`. It MUST validate the code (format, check digit,
  status, expiration), create the user, mark the invite redeemed, generate a
  `device_secret`, persist its SHA-256 hash, and return `{jwt, device_secret,
  user}`. The operation is transactional and idempotent on `Idempotency-Key`.
- **FR-002**: The system MUST expose `POST /api/v1/auth/refresh` accepting
  `{device_secret}`. Constant-time hash comparison. Returns a new JWT.
- **FR-003**: The system MUST expose `GET /api/v1/auth/me` requiring a valid JWT,
  returning the user record. Side-effect: `users.last_seen_at = now()`.
- **FR-004**: JWT MUST be HS256 with claims `sub` (UUID, `users.public_id`), `iss`
  ("ai-plot-twist"), `aud` ("web"), `iat`, `exp` (iat + 90 d), `jti` (UUID v4).
  Signing key from `JWT_SECRET` env var. Tokens MUST decode against the public
  `keys` endpoint (TBD; not exposed in this module — see OQ-AUTH-2).
- **FR-005**: A JWT middleware MUST be installed on a per-route basis (NOT global)
  and MUST verify: signature, `iss`, `aud`, `exp` (with 60 s leeway), and look up
  the user, rejecting `is_banned=true` with 403.
- **FR-006**: Invite codes MUST be 8 chars from RFC 4648 base32 alphabet (A–Z, 2–7),
  formatted as `XXXX-XXXX`, with the **last char** as a check digit computed as
  `base32(sha256(first 7 chars)[0:5])[0]`. Codes are case-insensitive on input;
  always uppercase on display and storage.
- **FR-007**: `pnpm issue-invite` MUST accept `--count N` (default 1),
  `--ttl-days D` (default 7), `--note TEXT`, `--display-name-hint TEXT`. It MUST
  print codes to stdout AND insert rows in `invites`. It MUST refuse to run if
  `ENV=prod` unless `--allow-prod` is passed (safety guard).
- **FR-008**: `pnpm revoke-invite --code XXXX-XXXX` MUST set the invite's
  `status='revoked'` if `unused`, or fail with a clear error otherwise.
  `pnpm list-invites` MUST print a table of all invites with their status.
- **FR-009**: Rate limiting on `POST /auth/redeem-invite` MUST allow at most 5
  attempts per source IP per 1-hour sliding window. The counter MUST be
  Postgres-backed (no Redis dependency).
- **FR-010**: `display_name` MUST be NFKC-normalized, control chars stripped,
  trimmed, then validated `2 ≤ len ≤ 24`. Duplicates ARE allowed (display name is
  not unique). Two users named "Lucía" can coexist.
- **FR-011**: All sensitive logs MUST scrub the raw `invite_code` and
  `device_secret`. Only the first 4 chars of the code appear in logs (for support).
  `device_secret` never appears in logs at any verbosity.
- **FR-012**: The `users.device_token` column MUST store the SHA-256 hash of the
  raw `device_secret` as a 64-char hex string. Constant-time comparison
  (`hmac.compare_digest`) on refresh.
- **FR-013**: The PWA MUST persist `jwt` and `device_secret` in IndexedDB under
  database `aiplottwist`, store `auth`, keys `jwt` and `device_secret`. On 401
  from a non-auth endpoint, the PWA MUST attempt one `/auth/refresh` before
  surfacing the error to the user.
- **FR-014**: A login screen MUST be added to the PWA at `/onboarding`, with an
  invite-code input (auto-formatting `XXXX-XXXX`) and a display-name input. After
  successful redemption, route to `/today` (placeholder until module 004).
- **FR-015**: The middleware MUST emit a structured log per authenticated request:
  `auth_check {user_public_id, decision, latency_ms}` where `decision ∈
  {allow, expired, banned, invalid_signature, unknown_user}`.

### Non-Functional Requirements

- **NFR-001**: `/auth/redeem-invite` p95 < 300 ms (includes hashing + insert).
- **NFR-002**: `/auth/refresh` p95 < 150 ms.
- **NFR-003**: `/auth/me` p95 < 100 ms.
- **NFR-004**: JWT verification middleware p95 < 20 ms per request (no DB call if
  the user object is cached in-process for 60 s).
- **NFR-005**: Resistance to timing attacks: redeem with a non-existent code MUST
  take within ±5 ms p50 of the time taken to redeem with a redeemed code (after
  the rate-limit check).

### Out of Scope (for this feature)

- Email-based recovery, magic links, OAuth, MFA.
- Multi-device per user (one `device_token` per `users` row in MVP). [OQ-AUTH-1]
- JWKS / public-key JWT (HS256 only). [OQ-AUTH-2]
- Self-service display-name change (deferred to a profile module).
- Admin web UI for invite management (CLI only).
- CSRF tokens (the PWA uses `Authorization` header, not cookies, so CSRF surface
  is minimal). Documented in research.md.
