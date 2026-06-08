# Task Breakdown: Auth via Invite Code + Device-Bound JWT

**Branch**: `002-auth-invite-flow` | **Date**: 2026-06-07

PR-sized chunks to land module 002. Each task names files, acceptance signal, and
dependencies. Tasks marked `[P]` parallelize within their phase; `→ T-NNN` blocks.

---

## Phase 0 — Migrations (3 PRs)

### T-001 — Alembic baseline bump for invites + users → 001-merged
**Files**:
- `apps/api/alembic/versions/0002_users_invites.py`

**Body**: as documented in [data-model.md](./data-model.md#0002_users_invitespy).

**Tests**:
- `tests/integration/test_migrations.py::test_0002_upgrade_then_downgrade`.

**Done when**: `alembic upgrade head` then `downgrade base` runs clean twice.

### T-002 — `rate_limit_buckets` migration [P]
**Files**:
- `apps/api/alembic/versions/0003_rate_limit_buckets.py`

**Body**: as documented in [data-model.md](./data-model.md#0003_rate_limit_bucketspy).

### T-003 — Repo seeds for tests → T-001, T-002
**Files**:
- `apps/api/tests/fixtures/invites.py`
- `apps/api/tests/fixtures/users.py`

**Behavior**: pytest fixtures that insert a known invite, a redeemed invite, and a
banned user. Used by integration tests in phases 4 and 5.

---

## Phase 1 — Domain layer (4 PRs)

### T-004 — `InviteCode` value object → T-001
**Files**:
- `apps/api/app/domain/invites.py`
- `apps/api/tests/unit/test_invite_code.py`

**API**:
```python
class InviteCode:
    raw: str               # canonical "XXXX-XXXX"
    @classmethod
    def parse(cls, s: str) -> "InviteCode": ...  # case-insensitive, hyphen optional
    @classmethod
    def generate(cls, rng=secrets) -> "InviteCode": ...
    def check_digit_valid(self) -> bool: ...
```

**Tests**: parse roundtrip, mutation rejection, deterministic generation under fixed
RNG, 10 000 random generations all pass `check_digit_valid()`.

### T-005 — `JWTService` → T-001
**Files**:
- `apps/api/app/domain/jwt_service.py`
- `apps/api/tests/unit/test_jwt_service.py`

**API**:
```python
class JWTService:
    def issue(self, user_public_id: UUID) -> tuple[str, datetime]:  # (jwt, exp)
    def verify(self, token: str) -> JWTClaims | None: ...           # None on any failure
```

**Tests**: issue + verify roundtrip; tampered signature → None; expired → None;
60 s leeway honored; wrong `aud` → None.

### T-006 — `DeviceSecret` helper [P]
**Files**:
- `apps/api/app/domain/device_secret.py`
- `apps/api/tests/unit/test_device_secret.py`

**API**:
```python
def mint() -> tuple[str, str]:                 # (raw_b64url, sha256_hex)
def verify(raw_b64url: str, stored_hash_hex: str) -> bool: ...   # constant time
```

### T-007 — Display-name normalizer [P]
**Files**:
- `apps/api/app/domain/display_name.py`
- `apps/api/tests/unit/test_display_name.py`

**Behavior**: NFKC + strip control chars + trim + validate length. Returns
`ValueError` on invalid.

---

## Phase 2 — Infra repos (3 PRs)

### T-008 — `InvitesRepo` → T-001, T-004
**Files**:
- `apps/api/app/infra/invites_repo.py`
- `apps/api/tests/integration/test_invites_repo.py`

**Methods**: `insert(code, expires_at, note, issued_by)`, `get_for_update(code)`,
`mark_redeemed(code, user_id)`, `revoke(code)`, `list_all()`.

### T-009 — `UsersRepo` → T-001
**Files**:
- `apps/api/app/infra/users_repo.py`
- `apps/api/tests/integration/test_users_repo.py`

**Methods**: `create(display_name, invite_code, device_token_hash)`,
`get_by_public_id(uuid)`, `touch_last_seen(public_id)`,
`get_by_device_token(hash)`.

### T-010 — `RateLimitRepo` → T-002
**Files**:
- `apps/api/app/infra/rate_limit_repo.py`
- `apps/api/tests/integration/test_rate_limit_repo.py`

**Method**: `check_and_increment(bucket_key, max_per_window) -> int` — atomic
UPSERT returning the new count. Raises `RateLimited` if `count > max`.

---

## Phase 3 — CLI scripts (3 PRs)

### T-011 — `issue-invite` CLI → T-004, T-008
**Files**:
- `apps/api/app/scripts/issue_invite.py`
- `apps/api/tests/integration/test_issue_invite_cli.py`
- `apps/api/package.json` (script: `"issue-invite": "uv run python -m
  app.scripts.issue_invite"`)
- `package.json` (root delegation: `"issue-invite": "pnpm --filter ./apps/api
  issue-invite --"`)

**Flags**: `--count N`, `--ttl-days D`, `--note T`, `--display-name-hint T`,
`--allow-prod`.

**Output**: pretty table to stdout; one row per code.

### T-012 — `revoke-invite` CLI → T-008
**Files**:
- `apps/api/app/scripts/revoke_invite.py`
- delegated scripts wired the same as T-011.

### T-013 — `list-invites` CLI → T-008
**Files**:
- `apps/api/app/scripts/list_invites.py`
- delegated scripts wired.

**Flags**: `--status STATUS`, `--expired-only`, `--json` (for grep-friendly output).

---

## Phase 4 — API endpoints (4 PRs)

### T-014 — JWT middleware → T-005
**Files**:
- `apps/api/app/middleware/jwt_auth.py`
- `apps/api/tests/integration/test_jwt_middleware.py`

**API**: `def require_user(request, session) -> User` — FastAPI Depends that
extracts `Authorization: Bearer`, decodes, looks up user (60 s LRU cache),
rejects banned. Returns the `User` domain object.

### T-015 — `POST /auth/redeem-invite` → T-014, T-008, T-009, T-010, T-006, T-007
**Files**:
- `apps/api/app/api/auth.py` (new router; this PR adds the redeem endpoint only)
- `apps/api/tests/integration/test_auth_redeem.py`

**Body**: as documented in [contracts/auth.yaml](./contracts/auth.yaml). Uses the
SQL transaction from [data-model.md §Redemption transaction](./data-model.md).

### T-016 — `POST /auth/refresh` → T-014, T-006
**Files**:
- `apps/api/app/api/auth.py` (extend)
- `apps/api/tests/integration/test_auth_refresh.py`

### T-017 — `GET /auth/me` → T-014, T-009
**Files**:
- `apps/api/app/api/auth.py` (extend)
- `apps/api/tests/integration/test_auth_me.py`

---

## Phase 5 — PWA persistence + UX (5 PRs)

### T-018 — IndexedDB wrapper → 001-merged
**Files**:
- `apps/web/src/lib/persistence.ts`
- `apps/web/tests/persistence.test.ts` (using `fake-indexeddb`)
- `apps/web/package.json` (devDep `fake-indexeddb`)

**API**:
```ts
export async function getAuth(): Promise<{jwt?: string; deviceSecret?: string}>
export async function setAuth(v: {jwt: string; deviceSecret: string}): Promise<void>
export async function clearAuth(): Promise<void>
```

### T-019 — Auth store (Svelte 5 runes) → T-018
**Files**:
- `apps/web/src/lib/auth-store.ts`
- `apps/web/tests/auth-store.test.ts`

**API**:
```ts
export const authStore = {
  jwt: $state<string | null>(null),
  user: $state<PublicUser | null>(null),
  init(): Promise<void>,          // loads from IndexedDB on app boot
  setSession(jwt, deviceSecret, user): Promise<void>,
  clear(): Promise<void>,
};
```

### T-020 — API client with refresh interceptor → T-018
**Files**:
- `apps/web/src/lib/api.ts`
- `apps/web/tests/api-interceptor.test.ts`

**Behavior**: typed fetch wrapper. On any non-2xx response with status 401, attempts
`/auth/refresh` exactly once with the stored device_secret; on success, replays
original request; on failure, clears auth and triggers a UI event. **Crucial**:
the interceptor has a single-flight guard (multiple concurrent 401s share one
refresh attempt).

### T-021 — Onboarding screen → T-019, T-020
**Files**:
- `apps/web/src/routes/onboarding.svelte`
- `apps/web/src/lib/code-input-mask.ts`
- `apps/web/tests/onboarding.test.ts`

**Behavior**: two inputs (code, display name). Code input auto-formats to
`XXXX-XXXX` as the user types. On submit, calls `/auth/redeem-invite`, persists
the response, routes to `/today`. Error UX:
- 404 → "Ese código no anda. Pedile uno nuevo al organizador."
- 409 → "Probá con otro nombre."
- 422 → "Revisá el nombre o el código."
- 429 → "Demasiados intentos. Probá en una hora."

### T-022 — Placeholder `/today` route + router → T-019
**Files**:
- `apps/web/src/routes/today.svelte`
- `apps/web/src/lib/router.ts`
- `apps/web/src/App.svelte` (modified)

**Behavior**: minimal SPA router (hash-based or `svelte-spa-router`). On boot,
calls `authStore.init()`. If `jwt` present, routes to `/today`; else `/onboarding`.

---

## Phase 6 — E2E + docs (2 PRs)

### T-023 — Playwright onboarding smoke → T-021, T-022
**Files**:
- `apps/web/tests/e2e/onboarding.spec.ts`
- `.github/workflows/ci.yml` (add web e2e job)

**Behavior**: spins up API + web in CI, calls `pnpm issue-invite` via Docker exec,
runs the onboarding flow in a headless browser, asserts the user lands on `/today`.

### T-024 — Quickstart + checklist verification → all prior
**Files**:
- `specs/002-auth-invite-flow/quickstart.md` (verified by a real walkthrough; PR
  description includes a screen-record).
- `specs/README.md` (mark module `done`, mark 003 `in-progress`).

---

## Done-when (module-level acceptance)

The module is "done" when:

1. All 24 tasks merged.
2. Every box in [checklists/requirements.md](./checklists/requirements.md) ticked.
3. A fresh user can be onboarded end-to-end through the PWA in < 60 s.
4. CI green; coverage on `apps/api/app/{domain,infra,api}/auth*` ≥ 90 %.

---

## Estimates (solo dev, calendar days)

| Phase | Tasks | Est. days |
|---|---|---|
| 0 — Migrations | T-001..T-003 | 1 |
| 1 — Domain | T-004..T-007 | 2 |
| 2 — Infra repos | T-008..T-010 | 1.5 |
| 3 — CLI | T-011..T-013 | 1 |
| 4 — API endpoints | T-014..T-017 | 2 |
| 5 — PWA | T-018..T-022 | 3 |
| 6 — E2E + docs | T-023..T-024 | 1 |
| **Total** | 24 tasks | **≈ 11.5 days** |

Buffer +30% for first-time Svelte 5 runes patterns and IndexedDB integration → **plan
for 15 working days**.
