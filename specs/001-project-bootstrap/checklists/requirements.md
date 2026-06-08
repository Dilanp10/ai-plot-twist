# Requirements Checklist: Project Bootstrap

**Branch**: `001-project-bootstrap` | **Date**: 2026-06-07

A PR closing module 001 is **mergeable** only when every box in this checklist is
ticked. The reviewer goes through this file linearly, ticking as evidence is provided
in the PR description (logs, screenshots, links to CI runs).

---

## Functional Requirements

- [ ] **FR-001** — Repo layout matches `apps/`, `packages/`, `infra/`, `specs/`,
      `.specify/`, `.github/workflows/`. `git ls-tree -d HEAD` evidence.
- [ ] **FR-002** — Root `package.json` + `pnpm-workspace.yaml` define workspaces
      `apps/web` (and a delegation `apps/api`). Root scripts include `install`,
      `install:api`, `dev`, `test`, `test:api`, `test:web`, `check`, `check:api`,
      `check:web`, `format`, `format:api`, `format:web`, `db:up`, `db:down`,
      `db:reset`, `migrate`. Output of `pnpm run` (script list) pasted in PR.
      No `justfile` and no `Makefile` exist in the repo.
- [ ] **FR-003** — `apps/api/pyproject.toml` declares FastAPI on Python 3.11 and
      Pydantic ~=2.7. `uv.lock` committed and resolved.
- [ ] **FR-004** — `GET /healthz` returns the documented payloads on both healthy
      and DB-down scenarios. Both responses tested in `tests/test_health.py`.
      No exception text in any response body (verified by grep).
- [ ] **FR-005** — `POST /api/v1/internal/transition` validates HMAC + timestamp,
      returns `202 {"status":"accepted","noop":true}` on success, `401` on bad
      HMAC, `409` on drift, `503` on missing `TICK_SECRET`. All four paths tested.
- [ ] **FR-006** — `DATABASE_URL` is the only DB config knob; async engine
      (`asyncpg`) is wired in `app/db.py`; the docker-compose dev DB is used
      end-to-end in CI integration tests.
- [ ] **FR-007** — Alembic baseline (`0001_baseline.py`) creates **only**
      `idempotency_keys` and enables `pgcrypto`. `alembic downgrade base` then
      `alembic upgrade head` runs clean in CI.
- [ ] **FR-008** — `apps/web/package.json` declares Svelte 5 (or 4 with documented
      reason) and Vite 5; `pnpm-lock.yaml` committed and frozen-install passes.
- [ ] **FR-009** — `manifest.webmanifest` is valid; service worker registers; the
      Lighthouse PWA audit gives "installable" verdict (screenshot in PR).
- [ ] **FR-010** — `mypy --strict app/` runs with zero errors. `tsc --noEmit` runs
      with zero errors. Counts of `# type: ignore` and `any` are zero (or
      documented exceptions listed in `mypy.ini` / ESLint config).
- [ ] **FR-011** — `ruff check` is clean on the repo. ESLint + Prettier are clean
      on the web. `pnpm format` is idempotent (running it twice produces no diff).
- [ ] **FR-012** — Test suite includes the listed coverage: router-mount unit
      tests, `/healthz` integration test against a real Postgres, vitest smoke
      for the web entry. Coverage report attached.
- [ ] **FR-013** — `.github/workflows/ci.yml` runs on PRs and pushes to `main`;
      uses Postgres 16 service container; completes ≤ 5 min p95 (link to last 5
      runs in PR).
- [ ] **FR-014** — Four `tick-*.yml` workflows exist with `workflow_dispatch`
      only; the `schedule:` block is present-but-commented and includes both UTC
      and ART times. Manual run from the GitHub UI POSTs successfully to
      `/internal/transition` and gets a 202.
- [ ] **FR-015** — `infra/fly.toml` configured for `shared-cpu-1x` / 256 MB,
      region `gru`, `auto_stop_machines = false`, healthcheck at `/healthz`.
      A successful `fly deploy` run is linked in PR.
- [ ] **FR-016** — `.env.example` lists every env var the code reads, with
      placeholder values and one-line comments. `.env.local` is gitignored.
      Application boots with only the documented env vars.
- [ ] **FR-017** — `README.md` covers prereqs, install, dev, deploy, troubleshoot
      sections, user-facing text in Spanish, code blocks in English.

## Non-Functional Requirements

- [ ] **NFR-001** — Cold `pnpm install` ≤ 3 min on the reviewer's machine
      (reviewer attests in PR comment).
- [ ] **NFR-002** — Cold `pnpm dev` reaches a healthy `/healthz` in ≤ 30 s
      (reviewer attests).
- [ ] **NFR-003** — CI p95 ≤ 5 min over the last 10 runs (link to CI insights).
- [ ] **NFR-004** — `k6 run scripts/k6/healthz_smoke.js` reports p95 < 200 ms
      at 50 RPS for 60 s against the deployed Fly URL. (Smoke script ships in
      this module too; reviewer runs once.)

## Constitution Gates

- [ ] **Gate 1 — Zero-Cost**: no paid service introduced. Verified by
      reviewer reading dep list and infra config.
- [ ] **Gate 2 — Idempotency**: `idempotency_keys` table exists; HMAC stub
      protects against trivial replay via timestamp tolerance.
- [ ] **Gate 3 — Timezone**: no naive `utcnow()` in the codebase
      (`grep -rn 'utcnow' apps/api/app` returns nothing).
- [ ] **Gate 4 — Provider abstraction**: N/A (vacuously satisfied).
- [ ] **Gate 5 — Determinism**: N/A (vacuously satisfied).
- [ ] **Gate 6 — Spanish UI / English code**: identifiers checked by reviewer;
      placeholder PWA strings in Spanish.
- [ ] **Gate 7 — Soft delete**: N/A (no user content).
- [ ] **Gate 8 — Tests from day one**: all categories of tests listed in FR-012
      land in the same PR as the feature.
- [ ] **Gate 9 — Trust boundaries**: HMAC enforced; `tests/test_internal_transition.py`
      exercises bad-signature, missing-signature, drifted-timestamp, and missing-
      secret paths.
- [ ] **Gate 10 — Observability**: `structlog` configured; every `/healthz` and
      `/internal/transition` request emits a JSON log with `request_id`,
      `outcome`, `duration_ms`.

## Documentation

- [ ] `README.md` updated.
- [ ] `specs/001-project-bootstrap/quickstart.md` matches the actual commands.
- [ ] `specs/README.md` module table marks `001-project-bootstrap` as `done`.
- [ ] No dangling `TODO` or `FIXME` in shipped code without a tracking issue link.

## Sign-off

- [ ] Reviewer 1 (engineering) — name, date.
- [ ] Reviewer 2 (Product Owner) — name, date.
- [ ] Constitution amendment required? If yes, link to ADR.
