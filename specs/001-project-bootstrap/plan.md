# Implementation Plan: Project Setup and Bootstrap

**Branch**: `001-project-bootstrap` | **Date**: 2026-06-07 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-project-bootstrap/spec.md`

## Summary

Stand up the `ai-plot-twist` monorepo skeleton: `apps/api/` (FastAPI 3.11 + uv + SQLAlchemy
2 async + Alembic + Pydantic v2 + ruff + mypy strict), `apps/web/` (Svelte 5 + Vite + pnpm
+ vitest), Docker-managed PostgreSQL 16 for local dev, baseline Alembic migration creating
only `idempotency_keys`, `GET /healthz` returning DB-checked status, `POST
/internal/transition` HMAC-stubbed for module 003 to flesh out later, a **root-level
`package.json` + `pnpm-workspace.yaml`** that orchestrates both the Python API (delegating
to `uv`) and the TypeScript web (delegating to pnpm workspaces), a CI workflow gating PRs,
four `tick-*.yml` workflows shipped in a **disabled** state, and a `fly.toml` ready for
`fly deploy`. No business logic.

## Technical Context

**Languages/Versions**: Python 3.11 (API), TypeScript 5.4+ (web)
**Primary Dependencies (API)**: FastAPI ~=0.115, Uvicorn ~=0.30, SQLAlchemy ~=2.0,
asyncpg ~=0.29, Alembic ~=1.13, Pydantic ~=2.7, pydantic-settings ~=2.3, structlog ~=24.1,
httpx ~=0.27 (for outbound), python-dateutil ~=2.9
**Primary Dependencies (web)**: Svelte 5, Vite 5, vite-plugin-pwa ~=0.20, typescript 5.4,
vitest ~=2.0, @playwright/test ~=1.45 (optional smoke)
**Tooling**: uv (Python deps), pnpm 9 (Node deps + workspace task runner via root
`package.json` scripts), `concurrently` (parallel API+web boot), ruff (lint+format
Python), mypy (strict), eslint + prettier (TS), Docker + Compose v2
**Storage**: PostgreSQL 16 (Neon in prod, Docker in dev)
**Testing**: pytest ~=8.2, pytest-asyncio ~=0.23, httpx test client, testcontainers
~=4.7 OR a docker-compose fixture, vitest, optional Playwright smoke for the placeholder
PWA route
**Target Platform**: API on Fly.io free tier (1× shared-cpu-1x, 256 MB, region `gru` —
closest to Argentina). Web on Cloudflare Pages.
**Project Type**: Polyglot monorepo (Python API + TS web), no inter-language code sharing
yet (module 010 may introduce a `packages/shared-schemas` later).
**Performance Goals**: Cold install ≤ 3 min; cold dev start ≤ 30 s; CI ≤ 5 min p95;
deployed `/healthz` p95 < 200 ms at 50 req/s.
**Constraints**: USD 0/month operating cost. No paid SaaS. No global state mutation
outside the repo on `pnpm install` (the uv postinstall must stay scoped to
`apps/api/.venv`).
**Scale/Scope**: Solo developer skeleton. Production traffic starts at module 010.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### Gate 1 — Zero-Cost Discipline
- [x] All services chosen are within documented free tiers: Fly.io (3 shared VMs free),
      Neon (0.5 GB free), Cloudflare Pages (unlimited bandwidth free), GitHub Actions
      (2000 min/mo free). No paid SaaS introduced.

### Gate 2 — Idempotency Everywhere
- [x] `idempotency_keys` table created in baseline migration (used by every future
      mutating endpoint).
- [x] `POST /internal/transition` stub validates `trigger_id` uniqueness against a future
      `state_transitions` table — for this feature the stub simply rejects duplicates
      seen in-process (no persistence yet); persistence lands in module 003.
- [x] `GET /healthz` is naturally idempotent.

### Gate 3 — Timezone Anchoring
- [x] Plan introduces no time computations. The HMAC stub uses `ts` from the request
      body and compares with `datetime.now(tz=timezone.utc)`. No DST surprises possible.

### Gate 4 — Provider Abstraction
- [x] No LLM/T2I usage in this feature. Gate vacuously satisfied.

### Gate 5 — Determinism
- [x] No business algorithms. Vacuously satisfied.

### Gate 6 — Spanish UI, English Code
- [x] All identifiers in English. Placeholder PWA page renders in Spanish
      ("Hola, esto es AI Plot Twist — bootstrap OK").
- [x] No new domain terms.

### Gate 7 — Soft Delete on User Content
- [x] No user content tables. Vacuously satisfied.

### Gate 8 — Tests from Day One
- [x] Unit test for `/healthz` route mount.
- [x] Integration test for `/healthz` against ephemeral Postgres.
- [x] Smoke test for PWA placeholder route.
- [x] CI workflow gates merges.

### Gate 9 — Trust Boundaries
- [x] `/internal/transition` requires `X-Tick-Signature` (HMAC-SHA256) and validates
      `ts ± 300 s`.
- [x] No JWT auth yet (module 002), so no user-authenticated endpoints exist.
- [x] No LLM outputs to validate yet.

### Gate 10 — Observability Minimum
- [x] `structlog` configured with JSON output to stdout. Every request gets a
      `request_id` (uuid4) injected by middleware. `/healthz` and `/internal/transition`
      log `outcome`, `duration_ms`.

## Project Structure

### Documentation (this feature)

```text
specs/001-project-bootstrap/
├── plan.md              ← this file
├── spec.md
├── research.md          ← Phase 0 decisions
├── data-model.md        ← baseline migration table
├── contracts/
│   └── health.yaml      ← OpenAPI for GET /healthz
├── quickstart.md        ← local setup + first deploy
├── checklists/
│   └── requirements.md  ← FR / NFR acceptance checklist
└── tasks.md             ← work-breakdown
```

### Repository (after this feature)

```text
ai-plot-twist/
├── README.md
├── SDD.md
├── package.json                 ← root orchestrator (pnpm scripts)
├── pnpm-workspace.yaml
├── pnpm-lock.yaml
├── .gitignore
├── .env.example
├── .specify/
│   └── memory/constitution.md
├── specs/
│   ├── README.md
│   └── 001-project-bootstrap/...
├── apps/
│   ├── api/
│   │   ├── pyproject.toml
│   │   ├── uv.lock
│   │   ├── mypy.ini
│   │   ├── ruff.toml
│   │   ├── alembic.ini
│   │   ├── alembic/
│   │   │   ├── env.py
│   │   │   ├── script.py.mako
│   │   │   └── versions/
│   │   │       └── 0001_baseline.py
│   │   ├── app/
│   │   │   ├── __init__.py
│   │   │   ├── main.py                 ← FastAPI app factory
│   │   │   ├── settings.py             ← pydantic-settings (env)
│   │   │   ├── db.py                   ← async engine + session
│   │   │   ├── logging.py              ← structlog config
│   │   │   ├── middleware/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── request_id.py
│   │   │   │   └── hmac_tick.py
│   │   │   └── api/
│   │   │       ├── __init__.py
│   │   │       ├── health.py
│   │   │       └── internal_transition.py
│   │   └── tests/
│   │       ├── conftest.py             ← async client + ephemeral db
│   │       ├── test_health.py
│   │       └── test_internal_transition.py
│   └── web/
│       ├── package.json          ← workspace member
│       ├── tsconfig.json
│       ├── vite.config.ts
│       ├── index.html
│       ├── public/
│       │   ├── manifest.webmanifest
│       │   └── icons/ (placeholder)
│       └── src/
│           ├── main.ts
│           ├── App.svelte
│           └── lib/
│               └── version.ts
├── packages/                ← reserved, empty
├── infra/
│   ├── fly.toml
│   ├── docker-compose.dev.yml
│   └── README.md
└── .github/
    └── workflows/
        ├── ci.yml
        ├── tick-12-estreno.yml          ← disabled (workflow_dispatch only)
        ├── tick-18-vote.yml             ← disabled
        ├── tick-23-generate.yml         ← disabled
        └── tick-2355-watchdog.yml       ← disabled
```

## Phase 0 — Research

See [research.md](./research.md) for the full record. Key decisions resolved:

- **Why `uv` instead of poetry?** Faster cold installs, lockfile parity with cargo
  ergonomics, official Astral support, no Python-version drift issues.
- **Why pnpm scripts (root `package.json`) instead of `just` / `make`?** Zero extra
  prerequisite on contributor machines (pnpm is already required for the web), works
  on Windows/mac/Linux identically, and `concurrently` handles parallel API+web boot
  without a separate binary. The trade-off is that Python-only contributors still
  install Node — accepted for a polyglot monorepo.
- **Why Svelte 5 (not Svelte 4)?** In 2026-06, Svelte 5 has been GA for ~18 months;
  the runes API is the documented default; TypeScript inference is materially better;
  bundle size ~10% smaller; choosing Svelte 4 would mandate a future migration with
  no upside for a greenfield app. Fallback to Svelte 4 documented in R-B5 if a critical
  library lacks v5 support.
- **Why ship cron workflows disabled?** Avoid spurious tick payloads hitting an
  unimplemented endpoint on a still-private deployment. Module 003 flips them on.
- **Why Neon (not Supabase or Railway PG)?** Branchable DB on free tier, generous PITR,
  zero-egress within Cloudflare network, official async driver compatibility.

## Phase 1 — Design Artefacts

- [contracts/health.yaml](./contracts/health.yaml) — OpenAPI for `GET /healthz`.
- [data-model.md](./data-model.md) — baseline schema (`idempotency_keys` only).
- [quickstart.md](./quickstart.md) — `clone → install → dev → deploy` walkthrough.
- [checklists/requirements.md](./checklists/requirements.md) — FR/NFR verification grid.
- [tasks.md](./tasks.md) — PR-sized work-breakdown.

## Phase 2 — Implementation Sequence

1. **T-001 to T-003** — Repo skeleton (root `package.json` + `pnpm-workspace.yaml`,
   `.gitignore`, `.env.example`, `README.md`, constitution + index).
2. **T-004 to T-009** — API skeleton (`pyproject.toml`, `app/main.py`, `app/settings.py`,
   `app/db.py`, `app/logging.py`, ruff + mypy config).
3. **T-010 to T-012** — Alembic baseline + `idempotency_keys` table + downgrade test.
4. **T-013 to T-016** — `GET /healthz` endpoint + integration test + OpenAPI export.
5. **T-017 to T-020** — `POST /internal/transition` HMAC stub + middleware + tests.
6. **T-021 to T-025** — Web skeleton (Vite + Svelte + PWA plugin + placeholder route).
7. **T-026 to T-028** — Docker compose dev DB + scripts.
8. **T-029 to T-031** — CI workflow + matrix on PR.
9. **T-032 to T-035** — Four `tick-*.yml` workflows (disabled).
10. **T-036 to T-038** — `fly.toml` + deploy walkthrough + smoke test against live
    `/healthz`.

See [tasks.md](./tasks.md) for the full breakdown with file paths and acceptance
criteria per task.

## Risks & Mitigations (feature-local)

| ID | Risk | Mitigation |
|---|---|---|
| **R-B1** | `uv` lockfile drift between dev and CI | CI installs from `uv.lock` exactly via `uv sync --frozen`. PR fails if lockfile is dirty. |
| **R-B2** | `testcontainers` is slow on Windows runners | Fallback to a `services:` Postgres in `ci.yml`; testcontainers only used locally. |
| **R-B3** | Fly.io free tier suspends the machine mid-test | `auto_stop_machines = false` + `min_machines_running = 1`. |
| **R-B4** | HMAC clock skew between GH Actions and Fly | Tolerance of ±300 s, well above GH Actions documented skew (<60 s). |
| **R-B5** | Svelte 5 still in flux at ship time | Pin minor version; if regressions surface, downgrade to Svelte 4 in a follow-up — no API change. |

## Post-Conditions

After this feature ships and is merged:

- The repo is buildable, testable, and deployable on a clean machine.
- The deployed API exposes only `/healthz` and the HMAC-stubbed `/internal/transition`.
- CI is green and required on PRs.
- Module 002 (auth) can branch from `main` and start implementing JWT issuance against
  this skeleton with zero re-work.
