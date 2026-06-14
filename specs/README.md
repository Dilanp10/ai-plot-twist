# AI Plot Twist — Specs Index

This folder holds one sub-folder per **feature module**, following the
[GitHub Spec Kit](https://github.com/github/spec-kit) layout. Each module is a
self-contained, independently-shippable slice with its own spec, plan, contracts,
data-model fragment, quickstart, checklists, and task breakdown.

The high-level architectural overview lives in [../SDD.md](../SDD.md). The non-negotiable
project rules live in [../.specify/memory/constitution.md](../.specify/memory/constitution.md).

## Module Roadmap

| # | Module | Status | Depends on | Summary |
|---|---|---|---|---|
| [001](./001-project-bootstrap/) | `project-bootstrap` | `done` | — | Repo skeleton (FastAPI + PWA), Fly.io + Neon + R2 + Pages + GH Actions cron heartbeat; `GET /healthz`; zero business logic. |
| [002](./002-auth-invite-flow/) | `auth-invite-flow` | `done` | 001 | Invite-code redemption + device-bound JWT (HS256, 90 d, refresh via `device_secret`), 3 endpoints, 3 CLIs, PWA onboarding screen. |
| [003](./003-cycle-fsm/) | `cycle-fsm` | `in-progress` | 001 | FSM 8 estados, advisory locks, idempotency UNIQUE, replace stub `/internal/transition` con executor real, kill-switch, watchdog, 4 cron workflows habilitados, stubs de side-effects para 006/008. |
| [004](./004-chapters-content/) | `chapters-content` | `spec-done` | 001, 003 | 3 endpoints read-only (`/chapters/today`, `/chapters/{id}`, `/seasons/{slug}`), bible redaction allowlist, windows computados server-side, ETag + Cache-Control + 304, R2 público (sin proxy), kill-switch + no-season handling. |
| [005](./005-twists-submission/) | `twists-submission` | `done` | 002, 003 | 3 endpoints autenticados (`POST/DELETE /twists/*`, `GET /me/twists`), advisory lock para quota race-safe, soft delete sin liberar quota, Idempotency-Key requerido, integración con cycle.state. |
| [006](./006-directors-filter/) | `directors-filter` | `spec-done` | 003, 005 | `LLMProvider` abstraction (reusada por 008), Gemini + GH Models con router fallback, prompts versionados con hash audit, default-deny fail-closed, slur post-filter (Gate 9 defense-in-depth), reemplaza stub via DI, admin replay endpoint. |
| [007](./007-voting/) | `voting` | `in-progress` | 002, 003, 005, 006 | Vote-feed con sort estable per-user (anti refresh-gaming), cursor pagination, UNIQUE constraint + ON CONFLICT como idempotencia natural, advisory lock para quota race, optimistic UI. |
| [008](./008-generation-pipeline/) | `generation-pipeline` | `spec-done` | 003, 006, 007, 009 | Winner selection determinística (SDD §4.3), scriptwriter LLM (reusa `LLMProvider` de 006), 2 prompts versionados (normal + auto-continue), panel pipeline paralelo con `ImageProviderRouter` (de 009), TTS Edge-TTS opcional best-effort, R2 upload boto3, deadline coordinator con asyncio race, ready_degraded para partial failures, admin rerun. |
| [009](./009-image-providers/) | `image-providers` | `spec-done` | 001 | `ImageProvider` ABC + Pollinations + HuggingFace + Fake; Router con fallback semantics tipadas (RateLimited→skip, Unavailable→retry+backoff, InvalidOutput→skip-no-retry); `compute_r2_path` content-addressed; `chain_for_env(mvp/dev/v02)`; LocalComfy stub reservado para v0.2; import-graph guard test. |
| [010](./010-pwa-client/) | `pwa-client` | `spec-done` | 002, 004, 005, 007 | App shell + bottom nav + route resolver por cycle_state; SW workbox strategies (precache + SWR + cacheFirst); install Android (`beforeinstallprompt`) + iOS sheet; Settings + sign-out completo; ErrorBoundary + client-logger; `/internal/client-log` endpoint; A11y WCAG 2.2 AA; Lighthouse CI gate (perf ≥ 85, a11y ≥ 95, PWA ≥ 90); CSP en 2 fases. |
| [011](./011-web-push/) | `web-push` | `spec-done` | 002, 003, 010 | `push_subscriptions` table, VAPID via pywebpush, 3 endpoints + admin test, `push_fanout` side-effect spawnado por executor de 003 en transición ESTRENO, idempotency en `push_fanout:<uuid>`, cleanup en 410 (hard delete con ADR-0007 carve-out de Gate 7), SW handlers (push + notificationclick), Settings toggle con 3 estados de Notification.permission. |

## Working Sequence

The modules are designed so that 001 → 002 → 003 unlocks 80% of the surface area; after
that, 004–011 can ship in parallel by different contributors as long as they respect the
declared dependencies.

## How to Read a Module

Open the module folder and read in this order:

1. `spec.md` — what the user / system must be able to do.
2. `plan.md` — how it will be built (stack, structure, gates).
3. `contracts/*.yaml` — OpenAPI for the endpoints exposed.
4. `data-model.md` — DB tables introduced or modified.
5. `research.md` — alternatives evaluated and why this path won.
6. `quickstart.md` — how to run and verify it locally.
7. `checklists/requirements.md` — acceptance bar for "done".
8. `tasks.md` — work-breakdown into PR-sized chunks.
