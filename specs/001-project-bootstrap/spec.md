# Feature Specification: Project Setup and Bootstrap

**Feature Branch**: `001-project-bootstrap`
**Created**: 2026-06-07
**Status**: Draft
**Depends on**: —

## Summary

Bootstrap the `ai-plot-twist` repository skeleton so that a developer can clone, install,
and run both the API (FastAPI) and the web client (Svelte PWA) on a clean machine in less
than 5 minutes, with a healthy database connection and the GitHub Actions cron heartbeat
already wired (but disabled by default). This feature ships **zero** business logic — no
auth, no cycles, no twists, no LLM calls, no image generation. The only delivered endpoint
is `GET /healthz`.

## User Scenarios & Testing

### User Story 1 — Developer bootstraps the project from scratch (Priority: P1)

A developer clones the repo onto a clean machine with only Python 3.11, Node 20, Docker,
`uv`, and `pnpm` pre-installed. They follow the README, run two commands (`pnpm install`
+ `pnpm dev`), and have the full local environment running: API responding on
`http://localhost:8000`, PWA on `http://localhost:5173`, and Postgres reachable via Docker.

**Why this priority**: Every other module assumes this skeleton exists. No business
feature can be built until the harness works.

**Independent Test**: Wipe a VM, install only the four prerequisites, clone the repo,
run the two documented commands, and confirm `curl localhost:8000/healthz` returns
`{"status":"ok","checks":{"database":"ok"}}`.

**Acceptance Scenarios**:

1. **Given** a clean machine with Python 3.11, Node 20, Docker, `uv`, and `pnpm`
   installed,
   **When** the developer runs `pnpm install` from the repo root,
   **Then** all Python dependencies (delegated via `uv sync --frozen` in `apps/api/`)
   and Node dependencies (`pnpm install --frozen-lockfile` recursively) are installed
   without errors and without modifying any global state outside the repo.

2. **Given** dependencies are installed,
   **When** the developer runs `pnpm dev` from the repo root,
   **Then** the local Postgres container starts (`pnpm db:up`), Alembic applies the
   baseline migration (`pnpm migrate`), and `concurrently` boots the FastAPI app on
   `:8000` and the Vite dev server on `:5173`, all within 30 seconds.

3. **Given** the API is running and the database container is healthy,
   **When** the developer calls `GET /healthz` without authentication,
   **Then** the response is HTTP 200 with body `{"status":"ok","checks":{"database":"ok"}}`.

4. **Given** the API is running but the database container is stopped,
   **When** the developer calls `GET /healthz`,
   **Then** the response is HTTP 503 with body `{"status":"error","checks":{"database":"error"}}`
   and the API process does NOT crash.

5. **Given** the web dev server is running,
   **When** the developer opens `http://localhost:5173` in a browser,
   **Then** a placeholder home page loads, the service worker registers, and DevTools
   shows the manifest as a valid installable PWA.

6. **Given** the repo is freshly cloned,
   **When** the developer runs `pnpm test`,
   **Then** all placeholder tests pass (`pytest` for API, `vitest` for web) and the
   command exits with code 0.

7. **Given** the repo is freshly cloned,
   **When** the developer runs `pnpm check` (lint + type-check),
   **Then** `ruff check`, `mypy --strict app/`, `eslint`, and `tsc --noEmit` all
   complete with no errors.

### User Story 2 — CI validates every PR (Priority: P1)

When a developer opens a pull request, GitHub Actions runs the full check suite — lint,
type-check, unit tests, and a smoke test against an ephemeral Postgres — and blocks the
merge on any failure.

**Why this priority**: Without CI from day one, drift is inevitable.

**Independent Test**: Open a PR that breaks lint. CI must fail. Fix the lint error. CI
must pass. Merge button must reflect the gate.

**Acceptance Scenarios**:

1. **Given** a pull request is opened against `main`,
   **When** the `ci.yml` workflow runs,
   **Then** it executes `pnpm check` and `pnpm test` against an ephemeral Postgres 16
   service container and reports status back to GitHub within 5 minutes.

2. **Given** a pull request introduces a lint error,
   **When** CI runs,
   **Then** the workflow fails with a non-zero exit and the PR cannot be merged.

### User Story 3 — Operator deploys to Fly.io (Priority: P2)

An operator with a Fly.io account, a Neon connection string, and a Cloudflare R2 bucket
can deploy the API in one command and verify the deployed `/healthz` from the public URL.

**Why this priority**: We need at least one successful production-shaped deploy before
shipping the first business module, to flush out env-var, secret, and network surprises.

**Acceptance Scenarios**:

1. **Given** the operator has set the documented Fly secrets (`DATABASE_URL`,
   `JWT_SECRET`, `TICK_SECRET`, `R2_*`),
   **When** they run `fly deploy --config infra/fly.toml`,
   **Then** the app is built, pushed, and live within 5 minutes, and
   `curl https://<app>.fly.dev/healthz` returns 200 with `database: ok`.

2. **Given** the app is live,
   **When** GitHub Actions runs the `tick-2355-watchdog.yml` workflow with the
   `WATCHDOG` payload,
   **Then** the request is accepted with HTTP 202 and the health check returns
   normally (state engine itself is not implemented yet, so the watchdog only verifies
   HMAC and replies "no-op").

### Edge Cases

- **Port collisions**: if `:8000` or `:5173` is already in use, `pnpm dev` MUST fail
  with a clear message identifying which port collided. No silent fallback to another
  port.
- **Missing prerequisites**: if Docker is not running, `pnpm dev` MUST fail with a
  message instructing the developer to start Docker, not a cryptic socket error.
- **Stale virtualenv**: if `uv sync` detects a mismatched lockfile, it MUST regenerate
  the environment without manual intervention.
- **R2 misconfigured in dev**: the API MUST start successfully even if R2 credentials
  are absent or invalid in `.env.local`; any code path that needs R2 fails lazily at
  call time, not at boot.
- **HMAC tick without `TICK_SECRET`**: if the env var is missing, every request to
  `POST /internal/*` MUST return 503 and the boot log MUST emit a single warning
  (no per-request log spam).

## Requirements

### Functional Requirements

- **FR-001**: The repo MUST be a single git repository organized as `apps/api/` (Python),
  `apps/web/` (TypeScript), `packages/` (reserved, empty), `infra/` (Fly + docker-compose),
  `specs/`, `.specify/`, `.github/workflows/`.
- **FR-002**: The repo MUST ship a top-level `package.json` with `pnpm-workspace.yaml`
  defining workspaces `apps/web` and (a thin orchestration package for) `apps/api`. The
  root `package.json` MUST expose exactly these scripts: `install`, `dev`, `test`,
  `test:api`, `test:web`, `check`, `check:api`, `check:web`, `format`, `db:up`,
  `db:down`, `db:reset`, `migrate`. Cross-language orchestration (running uvicorn + vite
  in parallel, calling `uv` from a script) MUST use `concurrently` and short shell
  one-liners — no separate task runner binary (no `just`, no `make`).
- **FR-003**: The API MUST be built with FastAPI on Python 3.11, dependencies managed by
  `uv` with a committed `uv.lock`. The Pydantic version MUST be v2.
- **FR-004**: The API MUST expose `GET /healthz` (unauthenticated). On success: HTTP 200
  with `{"status":"ok","checks":{"database":"ok"}}`. On any check failure: HTTP 503 with
  the failing check set to `"error"`. The body schema is extensible: future modules may
  add keys (e.g. `"llm":"ok"`) without breaking existing consumers. No stack traces,
  exception messages, or secrets appear in the body.
- **FR-005**: The API MUST expose `POST /internal/transition` (HMAC-protected). In this
  feature, the endpoint is a **stub** that validates the signature (`X-Tick-Signature`
  using HMAC-SHA256 of the body with `TICK_SECRET`) and the timestamp (within ±300 s of
  `now()`), then returns HTTP 202 with `{"status":"accepted","noop":true}`. Real state
  transitions are implemented in module 003.
- **FR-006**: The API MUST connect to Postgres via SQLAlchemy 2.x async engine. The
  database URL MUST come from the `DATABASE_URL` env var. Local development uses the
  Postgres 16 Docker container defined in `infra/docker-compose.dev.yml`.
- **FR-007**: Schema migrations MUST be managed by Alembic. The baseline migration MUST
  create exactly one table: `idempotency_keys` (used by all future modules). No business
  tables are introduced in this feature.
- **FR-008**: The web client MUST be Svelte 5 (or Svelte 4 if 5 is not yet stable at
  ship time) built with Vite. Package manager: `pnpm`. The dev server runs on `:5173`.
- **FR-009**: The web client MUST register a service worker and ship a valid
  `manifest.webmanifest` such that Lighthouse's PWA audit gives an installable verdict.
  The home page is a single placeholder route with the project name and version.
- **FR-010**: Static type-checking MUST be strict: `mypy --strict app/` for the API and
  `tsc --noEmit` against `strict: true` for the web. No `# type: ignore` in the API
  outside of documented third-party gaps; no `any` in web code outside of typed
  third-party gaps.
- **FR-011**: Linting and formatting MUST use `ruff` (configured for line-length 100,
  enabled rule sets: `E,F,I,B,UP,SIM,RUF`) for Python and `eslint` + `prettier` for
  TypeScript. `pnpm format` rewrites files in place; `pnpm check` only verifies.
- **FR-012**: The test suite MUST include:
  - one Python unit test per FastAPI router (asserting the route is mounted and returns
    a 2xx for the documented happy path),
  - one Python integration test for `GET /healthz` against an ephemeral Postgres using
    `testcontainers` or a fixture-managed Docker container,
  - one Vitest test for the web entry point (renders without errors).
- **FR-013**: A `.github/workflows/ci.yml` workflow MUST run on every PR and push to
  `main`. It MUST execute `pnpm check` and `pnpm test` against a Postgres 16 service
  container and complete in under 5 minutes.
- **FR-014**: The repo MUST ship **four** `.github/workflows/tick-*.yml` workflows
  (`tick-12-estreno`, `tick-18-vote`, `tick-23-generate`, `tick-2355-watchdog`) with
  cron schedules in UTC commented with their ART equivalents. Each workflow signs the
  payload with HMAC and POSTs to `${API_URL}/api/v1/internal/transition`. **All four
  workflows MUST be created in a disabled state** (manual `workflow_dispatch` only;
  the `schedule:` block is commented out). They will be enabled in module 003.
- **FR-015**: `fly.toml` MUST be present in `infra/` and configured for a single
  `shared-cpu-1x` 256 MB machine with `auto_stop_machines = false` and a healthcheck
  pointing to `/healthz`.
- **FR-016**: An `.env.example` MUST document every env var the application reads,
  with safe-default placeholder values and a one-line comment explaining each. Real
  secrets MUST be loaded from `.env.local` (gitignored) in dev and from Fly secrets in
  production.
- **FR-017**: A top-level `README.md` MUST explain prerequisites, install, dev,
  deploy, and troubleshoot — in that order, in Spanish (user-facing), with code blocks
  in English.

### Non-Functional Requirements

- **NFR-001**: First-run cold install (`pnpm install` on a clean checkout) MUST complete
  in ≤ 3 min on a 100 Mbps connection.
- **NFR-002**: Cold dev start (`pnpm dev` on a freshly booted machine, Docker already
  running) MUST reach a healthy `/healthz` in ≤ 30 s.
- **NFR-003**: CI workflow MUST complete in ≤ 5 min p95.
- **NFR-004**: The deployed API on Fly.io free tier MUST sustain 50 req/s on
  `/healthz` for 60 s with p95 < 200 ms (sanity check, not a production SLA).

### Out of Scope (for this feature)

- Auth, JWT issuance, invite codes (module 002).
- Any business table beyond `idempotency_keys` (modules 003+).
- Actual cron-driven state transitions (module 003).
- LLM or T2I integrations (modules 006, 008, 009).
- Push subscriptions (module 011).
- Observability stack beyond `structlog` to stdout (logging to an external sink is
  deferred).
- Pre-commit hooks (developer choice; documented as recommended).
