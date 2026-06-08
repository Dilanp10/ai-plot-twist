# Implementation Plan: Auth via Invite Code + Device-Bound JWT

**Branch**: `002-auth-invite-flow` | **Date**: 2026-06-07 | **Spec**: [spec.md](./spec.md)
**Depends on**: `001-project-bootstrap` (merged and deployed)

## Summary

Add the first authenticated identity to the system. Three endpoints
(`/auth/redeem-invite`, `/auth/refresh`, `/auth/me`), three Alembic migrations
(`users`, `invites`, `rate_limit_buckets`), one JWT middleware dependency, one PWA
onboarding screen + IndexedDB persistence layer, and three CLI scripts (`issue-invite`,
`revoke-invite`, `list-invites`). HS256 JWT with 90-day TTL refreshed via a
device-bound secret. No email, no password, no OAuth.

## Technical Context

**Languages/Versions**: Python 3.11, TypeScript 5.4+ (no change from module 001).
**New API dependencies**: `pyjwt~=2.9` (already declared in 001 for the HMAC stub but
now actually used), `argon2-cffi~=23.1` — wait, we don't hash passwords; using SHA-256
for the device_token, no extra dep needed. We do add `python-ulid~=2.7` for ULID-based
`jti` (more sortable than UUID v4). No new web deps; IndexedDB is browser-native.
**Storage**: 3 new tables (`users`, `invites`, `rate_limit_buckets`). No data migration.
**Testing**: pytest + httpx for endpoints; vitest + fake-indexeddb for PWA persistence.
**Target platform**: same as 001.
**Project type**: same.
**Performance Goals**: see NFR-001..NFR-005 in spec.
**Constraints**: no Redis (rate limit in PG). No external secret manager (JWT_SECRET
from Fly secrets).
**Scale/Scope**: closed beta, ≤ 100 users total in MVP.

## Constitution Check

### Gate 1 — Zero-Cost Discipline
- [x] No new paid services. PyJWT and python-ulid are open-source, install via uv.

### Gate 2 — Idempotency
- [x] `POST /auth/redeem-invite` honors `Idempotency-Key` header. Same key + same
      body within 14 days returns cached response. Different body with same key
      returns 409 `idempotency_conflict`.
- [x] `POST /auth/refresh` is naturally idempotent (no state changes; just issues
      a new JWT).

### Gate 3 — Timezone Anchoring
- [x] `iat`, `exp`, `expires_at`, `last_seen_at`, `redeemed_at` use
      `TIMESTAMPTZ` in DB. Python uses `datetime.now(tz=ZoneInfo("America/Argentina/Buenos_Aires"))`
      for human-facing logs; `datetime.now(timezone.utc)` for JWT claims (RFC 7519
      requires UTC seconds).

### Gate 4 — Provider Abstraction
- [x] N/A — no LLM/T2I in this module.

### Gate 5 — Determinism
- [x] Code check-digit derivation is deterministic. Same first 7 chars → same
      check digit.
- [x] JWT `jti` is non-deterministic (ULID), but it's a security ID — not a
      replayable value.

### Gate 6 — Spanish UI, English Code
- [x] All identifiers in English. Onboarding screen text in Spanish: "Ingresá tu
      código de invitación", "Elegí cómo te van a ver".

### Gate 7 — Soft Delete on User Content
- [x] Users are never hard-deleted in this module. Bans use `is_banned=true`.
      Future user-deletion (right-to-be-forgotten) deferred; not in MVP scope.
- [x] Invites use `status` column; revoking is a state change, not a row delete.

### Gate 8 — Tests from Day One
- [x] Unit tests for code check-digit, JWT encode/decode, time-leeway, banned
      user, rate-limit counting.
- [x] Integration tests for the three endpoints against a real Postgres.
- [x] PWA tests for IndexedDB persistence (fake-indexeddb).

### Gate 9 — Trust Boundaries
- [x] HMAC tick endpoint (from 001) untouched.
- [x] JWT middleware validates **signature**, **iss**, **aud**, **exp** + 60 s
      leeway. Per-route dependency (not global) so public endpoints stay public.
- [x] `device_secret` compared in constant time (`hmac.compare_digest`).
- [x] Rate limit is enforced **before** any DB lookup of the code (prevents
      DB-load amplification from brute force).
- [x] No info leak: 404 for non-existent, redeemed, revoked, expired codes
      collapses into one error code.

### Gate 10 — Observability
- [x] Per-request structured log `auth_check {user_public_id, decision,
      latency_ms}`.
- [x] CLI scripts log their actions to a `audit_log.jsonl` file in
      `apps/api/var/` (gitignored).

## Project Structure

### Documentation (this feature)

```text
specs/002-auth-invite-flow/
├── spec.md
├── plan.md              ← this file
├── research.md
├── data-model.md
├── contracts/
│   └── auth.yaml
├── quickstart.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### New / modified code

```text
apps/api/
├── alembic/versions/
│   ├── 0002_users_invites.py        ← NEW
│   └── 0003_rate_limit_buckets.py   ← NEW
├── app/
│   ├── domain/
│   │   ├── __init__.py              ← NEW (first business domain)
│   │   ├── invites.py               ← NEW (InviteCode value object, check digit)
│   │   ├── users.py                 ← NEW (User entity)
│   │   └── jwt_service.py           ← NEW (encode/decode/verify)
│   ├── infra/
│   │   ├── __init__.py              ← NEW
│   │   ├── invites_repo.py          ← NEW
│   │   ├── users_repo.py            ← NEW
│   │   └── rate_limit_repo.py       ← NEW
│   ├── api/
│   │   └── auth.py                  ← NEW (3 routes)
│   ├── middleware/
│   │   └── jwt_auth.py              ← NEW (FastAPI Depends)
│   └── scripts/
│       ├── issue_invite.py          ← NEW (CLI)
│       ├── revoke_invite.py         ← NEW (CLI)
│       └── list_invites.py          ← NEW (CLI)
└── tests/
    ├── unit/
    │   ├── test_invite_code.py
    │   ├── test_jwt_service.py
    │   └── test_rate_limit.py
    └── integration/
        ├── test_auth_redeem.py
        ├── test_auth_refresh.py
        └── test_auth_me.py

apps/web/
├── src/
│   ├── routes/
│   │   ├── onboarding.svelte        ← NEW
│   │   └── today.svelte             ← placeholder until module 004
│   ├── lib/
│   │   ├── api.ts                   ← NEW (fetch wrapper w/ refresh interceptor)
│   │   ├── auth-store.ts            ← NEW (Svelte 5 runes-based)
│   │   └── persistence.ts           ← NEW (IndexedDB wrapper)
│   └── App.svelte                   ← MODIFIED (router decides onboarding vs today)
└── tests/
    ├── auth-store.test.ts
    └── persistence.test.ts
```

## Phase 0 — Research

See [research.md](./research.md). Key decisions:

- **JWT algorithm: HS256** (symmetric, simplest, single trust boundary).
- **JWT lifetime: 90 d** with refresh via device_secret (better UX than 15-min +
  rotation; documented in research).
- **Invite format**: `XXXX-XXXX` base32 (RFC 4648 alphabet, no 0/1/I/L/O confusion)
  with 1-char check digit.
- **Device secret storage**: IndexedDB on the client; SHA-256 hash in DB.
- **Single-device per user** in MVP (one `device_token` column on `users`).
- **Rate limit**: PG sliding-window with 1-hour buckets, no Redis.
- **Ban enforcement**: DB lookup on every authenticated request (with 60 s
  in-process cache).

## Phase 1 — Design Artefacts

- [contracts/auth.yaml](./contracts/auth.yaml) — OpenAPI for 3 endpoints.
- [data-model.md](./data-model.md) — `users`, `invites`, `rate_limit_buckets`.
- [quickstart.md](./quickstart.md) — issue → redeem → call /me walkthrough.
- [checklists/requirements.md](./checklists/requirements.md) — acceptance grid.
- [tasks.md](./tasks.md) — PR-sized work-breakdown.

## Phase 2 — Implementation Sequence

1. **T-001..T-003** — Alembic migrations for `users`, `invites`,
   `rate_limit_buckets`.
2. **T-004..T-006** — Domain layer: `InviteCode`, `User`, `JWTService`.
3. **T-007..T-009** — Infra repos.
4. **T-010..T-013** — CLI scripts (`issue-invite`, `revoke-invite`, `list-invites`).
5. **T-014..T-017** — API endpoints + JWT middleware + rate-limit middleware.
6. **T-018..T-020** — PWA persistence + auth store + onboarding screen.
7. **T-021..T-023** — PWA fetch interceptor with refresh-on-401.
8. **T-024..T-026** — Integration + e2e smoke (onboarding flow).

See [tasks.md](./tasks.md) for the full breakdown.

## Risks & Mitigations (feature-local)

| ID | Risk | Mitigation |
|---|---|---|
| **R-A1** | JWT_SECRET leak | Rotate via Fly secrets; old JWTs invalidate on next refresh. No in-band kid yet → rotation requires forced re-login (acceptable for MVP). |
| **R-A2** | Brute force despite rate limit (distributed IPs) | Add CAPTCHA at module 010 if observed. Out of scope here. |
| **R-A3** | Device secret stolen from IndexedDB by malicious browser ext | Out of scope; document. Ban user, regenerate code. |
| **R-A4** | Clock skew breaks JWT exp | 60 s leeway on inbound. CI tests both directions. |
| **R-A5** | In-process user cache stale (banned user keeps acting) | TTL 60 s caps blast radius. Ban triggers an in-process invalidation broadcast — N/A in MVP single-machine; document for v0.2. |
| **R-A6** | PWA loses IndexedDB → orphaned user | Documented behavior. New invite from PO. |
| **R-A7** | Hot CLI machine writes `audit_log.jsonl` unbounded | Logrotate on the PO's machine; not server concern. |

## Post-Conditions

After merge:
- Closed-beta users can join with an invite code.
- All future modules (003+) can require authentication via `Depends(jwt_auth)`.
- The PWA has a working onboarding screen and a persistent session.
- Module 003 (cycle FSM) can start; it does NOT require auth (cron-only), but
  the JWT middleware is available for future authenticated FSM admin endpoints.
