# Task Breakdown: Project Bootstrap

**Branch**: `001-project-bootstrap` | **Date**: 2026-06-07

This file lists the PR-sized chunks needed to land module 001. Each task names the files
it creates/modifies, the acceptance signal, and the dependencies.

**PR strategy**: each task ID = one PR, unless explicitly bundled. Tasks marked
`[P]` are parallelizable (no inter-dependency); tasks marked `→ T-NNN` block on a prior
task.

---

## Phase 0 — Repo skeleton (3 PRs)

### T-001 — Top-level scaffolding [P]
**Files**:
- `README.md`
- `.gitignore`
- `.env.example`
- `LICENSE` (MIT, decided by PO)

**Done when**:
- `git clone` on a fresh machine shows the documented layout.
- `.env.example` lists every variable referenced in this module's code (none new beyond
  `DATABASE_URL`, `TICK_SECRET`, `JWT_SECRET`, `R2_*`).

### T-002 — Root `package.json` + workspace scripts [P]
**Files**:
- `package.json` (root, orchestrator)
- `pnpm-workspace.yaml`
- `apps/api/package.json` (delegation-only; declares scripts that wrap `uv run …`)

**Root scripts** (no-op echoes allowed where the implementation lands in a later
task — but the names must exist so PRs can wire them incrementally):
`install`, `install:api`, `dev`, `test`, `test:api`, `test:web`, `check`,
`check:api`, `check:web`, `format`, `format:api`, `format:web`, `db:up`,
`db:down`, `db:reset`, `migrate`.

**Dependencies declared at root**: `concurrently@^9`, `prettier@^3` (devDependencies).
No runtime deps at the root.

**Postinstall hook**: root `package.json` declares
`"postinstall": "cd apps/api && uv sync --frozen"` so `pnpm install` triggers the
Python install too.

**Done when**: `pnpm run` lists all scripts; `pnpm install` on a clean checkout
produces both `node_modules/` (workspace-aware) and `apps/api/.venv/`.

### T-003 — Constitution and specs index [P]
**Files**:
- `.specify/memory/constitution.md` (already shipped — verify)
- `specs/README.md` (already shipped — verify)
- `SDD.md` (already shipped — verify)

**Done when**: links in `specs/README.md` resolve.

---

## Phase 1 — API skeleton (6 PRs)

### T-004 — `pyproject.toml` + `uv.lock` → T-001
**Files**:
- `apps/api/pyproject.toml`
- `apps/api/uv.lock`
- `apps/api/ruff.toml`
- `apps/api/mypy.ini`

**Dependencies declared**: fastapi, uvicorn[standard], sqlalchemy[asyncio], asyncpg,
alembic, pydantic, pydantic-settings, structlog, httpx, python-dateutil, pyjwt,
pytest, pytest-asyncio.

**Done when**: `uv sync --frozen` in `apps/api/` succeeds; `uv run python -c "import
fastapi"` succeeds.

### T-005 — Settings module → T-004
**Files**:
- `apps/api/app/settings.py`
- `apps/api/app/__init__.py`
- `apps/api/tests/__init__.py`

**Behavior**: Loads `DATABASE_URL`, `TICK_SECRET`, `JWT_SECRET`, `R2_*`, `LOG_LEVEL`,
`ENV` (`dev|prod|test`). Validation at import time; missing required keys raise
clearly. R2 keys are optional.

**Test**: `tests/test_settings.py` covers (a) all defaults, (b) missing required raises,
(c) override via env.

### T-006 — Logging setup → T-004
**Files**:
- `apps/api/app/logging.py`

**Behavior**: structlog configured for JSON output to stdout; `add_log_level`,
`add_logger_name`, `TimeStamper(fmt="iso", utc=True)`. Exposes `get_logger(name)`.

### T-007 — DB engine + session → T-005
**Files**:
- `apps/api/app/db.py`

**Behavior**: Async engine from `settings.DATABASE_URL`, `async_sessionmaker`, and
a `get_session()` FastAPI dependency. No models defined here.

**Test**: `tests/test_db.py` opens a session against an ephemeral DB and runs
`SELECT 1`.

### T-008 — FastAPI app factory + middleware → T-007
**Files**:
- `apps/api/app/main.py`
- `apps/api/app/middleware/__init__.py`
- `apps/api/app/middleware/request_id.py`

**Behavior**: `create_app()` returns a configured FastAPI instance; mounts request-id
middleware (uuid4 per request, header `X-Request-Id`); mounts OpenAPI at `/openapi.json`;
disables docs in prod (`/docs` only in dev/test).

### T-009 — Alembic baseline → T-007
**Files**:
- `apps/api/alembic.ini`
- `apps/api/alembic/env.py`
- `apps/api/alembic/script.py.mako`
- `apps/api/alembic/versions/0001_baseline.py`

**Body**: as documented in [data-model.md](./data-model.md).

**Test**: `tests/test_migrations.py` runs `alembic upgrade head` then `downgrade base`
twice and asserts no orphan objects remain.

---

## Phase 2 — Endpoints (4 PRs)

### T-010 — `GET /healthz` route → T-008
**Files**:
- `apps/api/app/api/__init__.py`
- `apps/api/app/api/health.py`
- `apps/api/tests/test_health.py`

**Behavior**: SELECT 1 against the async engine with a 1 s timeout. Returns the
documented shapes. Logs `outcome` and `duration_ms`.

**Done when**: integration test covers healthy and DB-down paths.

### T-011 — HMAC middleware → T-005
**Files**:
- `apps/api/app/middleware/hmac_tick.py`
- `apps/api/tests/test_hmac.py`

**Behavior**: dependency for `/internal/*` routes that (a) loads the raw body, (b)
recomputes HMAC-SHA256 over it with `TICK_SECRET`, (c) compares to header in constant
time, (d) parses JSON body for `ts`, (e) checks `|now − ts| ≤ 300 s`. On failure,
raises `HTTPException` with the documented status codes.

### T-012 — `POST /internal/transition` stub → T-011, T-010
**Files**:
- `apps/api/app/api/internal_transition.py`
- `apps/api/tests/test_internal_transition.py`

**Behavior**: as documented in [contracts/health.yaml](./contracts/health.yaml).
In-process set keeps the last 10 000 `trigger_id` values to reject replays inside the
process lifetime (deferred persistence to module 003).

### T-013 — Export OpenAPI to spec → T-012
**Files**:
- `scripts/export_openapi.py`
- `apps/api/openapi.json` (generated, committed)

**Behavior**: script dumps the live OpenAPI to `apps/api/openapi.json`; CI fails if the
committed file is stale. Cross-checks at least the two operationIds defined in
`contracts/health.yaml`.

---

## Phase 3 — Web skeleton (5 PRs)

### T-014 — `package.json` + `tsconfig.json` + Vite config → T-001
**Files**:
- `apps/web/package.json`
- `apps/web/pnpm-lock.yaml`
- `apps/web/tsconfig.json`
- `apps/web/vite.config.ts`
- `apps/web/.eslintrc.cjs`
- `apps/web/.prettierrc`

**Done when**: `pnpm install --frozen-lockfile` succeeds; `pnpm tsc --noEmit` is clean.

### T-015 — Svelte placeholder app → T-014
**Files**:
- `apps/web/index.html`
- `apps/web/src/main.ts`
- `apps/web/src/App.svelte`
- `apps/web/src/lib/version.ts`

**Behavior**: renders "Hola, esto es AI Plot Twist — bootstrap OK" + a version string
read from `version.ts` (which reads `package.json` at build time).

### T-016 — PWA manifest + service worker → T-015
**Files**:
- `apps/web/public/manifest.webmanifest`
- `apps/web/public/icons/icon-192.png` (placeholder)
- `apps/web/public/icons/icon-512.png` (placeholder)
- `apps/web/vite.config.ts` (updated with `vite-plugin-pwa`)

**Done when**: Lighthouse PWA audit reports "installable".

### T-017 — Vitest smoke → T-015
**Files**:
- `apps/web/src/App.test.ts`
- `apps/web/vitest.config.ts`

**Test**: renders `<App/>`; asserts the title string is present.

### T-018 — `pnpm dev` wires everything → T-008, T-016, T-019
**Files**:
- `package.json` (root — real script bodies)
- `apps/api/package.json` (delegation scripts)
- `apps/web/package.json` (`dev` script: `vite --port 5173`)

**Behavior**: `pnpm dev` runs `pnpm db:up && pnpm migrate && concurrently
--kill-others-on-fail "pnpm --filter ./apps/api dev" "pnpm --filter ./apps/web dev"`.
The API dev script is `uv run uvicorn app.main:app --reload --port 8000`. Port
collisions on `:8000` or `:5173` MUST fail fast with a clear message (use `is-port-
reachable` or a tiny preflight script if uvicorn/vite's default error is unclear).

---

## Phase 4 — Local DB + CI (3 PRs)

### T-019 — Docker compose dev DB → T-007
**Files**:
- `infra/docker-compose.dev.yml`
- `infra/README.md`

**Service**: `postgres:16-alpine`, port `5433:5432`, volume named, env `POSTGRES_USER=app`,
`POSTGRES_PASSWORD=app`, `POSTGRES_DB=aiplottwist`. Healthcheck via `pg_isready`.

**Root scripts wired in `package.json`** (now real):
- `db:up` → `docker compose -f infra/docker-compose.dev.yml up -d postgres`
- `db:down` → `docker compose -f infra/docker-compose.dev.yml down`
- `db:reset` → `pnpm db:down && docker volume rm aiplottwist_pgdata || true && pnpm db:up && pnpm migrate`
- `migrate` → `pnpm --filter ./apps/api migrate` → delegates to `uv run alembic upgrade head`

### T-020 — CI workflow → T-013, T-017
**Files**:
- `.github/workflows/ci.yml`

**Jobs**:
- `setup`: shared step (composite action) — install Node 20, pnpm 9, Python 3.11, uv;
  run `pnpm install` (which triggers the uv postinstall).
- `api`: needs `setup`; runs `pnpm check:api` and `pnpm test:api`. Services:
  `postgres:16` mapped to `localhost:5432`; `DATABASE_URL` overridden for CI.
- `web`: needs `setup`; runs `pnpm check:web` and `pnpm test:web`.
- `openapi-fresh`: needs `setup`; runs `pnpm --filter ./apps/api openapi:export`
  followed by `git diff --exit-code` to ensure the committed file is current.

**Done when**: green on a no-op PR; red on an intentionally broken PR.

### T-021 — k6 smoke script → T-020
**Files**:
- `scripts/k6/healthz_smoke.js`

**Behavior**: 50 RPS for 60 s against `BASE_URL/healthz`, assert p95 < 200 ms.

---

## Phase 5 — Cron heartbeat (disabled) (1 PR)

### T-022 — Four `tick-*.yml` workflows → T-012
**Files**:
- `.github/workflows/tick-12-estreno.yml`
- `.github/workflows/tick-18-vote.yml`
- `.github/workflows/tick-23-generate.yml`
- `.github/workflows/tick-2355-watchdog.yml`

**Behavior**: all use `workflow_dispatch` only; `schedule:` block is present but
commented with both UTC and ART times. Each computes HMAC and POSTs to `${API_URL}/api/v1/internal/transition`
with the appropriate `to` value.

**Secrets required (documented)**: `API_URL`, `TICK_SECRET`.

**Done when**: each workflow run via the UI returns HTTP 202 and the log shows the body
and signature.

---

## Phase 6 — Deploy + smoke (2 PRs)

### T-023 — `fly.toml` → T-008
**Files**:
- `infra/fly.toml`
- `apps/api/Dockerfile`

**Config**: `shared-cpu-1x`, 256 MB, region `gru`, `auto_stop_machines = false`,
`min_machines_running = 1`, healthcheck `GET /healthz` every 15 s.

### T-024 — Deploy walkthrough → T-023
**Files**:
- `specs/001-project-bootstrap/quickstart.md` (already shipped — verify "First deploy"
  section is accurate after a real deploy)

**Done when**: a deploy from a fresh Fly account succeeds following only the quickstart;
a live `/healthz` returns 200; the `tick-2355-watchdog` workflow_dispatch run hits the
live URL and returns 202.

---

## Done-when (module-level acceptance)

The module is "done" when:

1. All 24 tasks above are merged.
2. Every box in [checklists/requirements.md](./checklists/requirements.md) is ticked.
3. The deployed Fly app shows `/healthz: ok` for 24 hours straight.
4. The `specs/README.md` module table marks `001-project-bootstrap` as `done` and
   `002-auth-invite-flow` as `in-progress`.

---

## Estimates (solo dev, calendar days)

| Phase | Tasks | Est. days |
|---|---|---|
| 0 — Skeleton | T-001..T-003 | 0.5 |
| 1 — API skeleton | T-004..T-009 | 2 |
| 2 — Endpoints | T-010..T-013 | 1.5 |
| 3 — Web skeleton | T-014..T-018 | 1.5 |
| 4 — Local DB + CI | T-019..T-021 | 1 |
| 5 — Cron disabled | T-022 | 0.5 |
| 6 — Deploy + smoke | T-023..T-024 | 1 |
| **Total** | 24 tasks | **≈ 8 days** |

Buffer of +30% for first-time-Fly-deploy and Svelte 5 unknowns → **plan for 10 working days**.
