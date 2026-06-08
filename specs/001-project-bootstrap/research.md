# Phase 0 Research: Project Setup and Bootstrap

**Branch**: `001-project-bootstrap` | **Date**: 2026-06-07

This document records the alternatives evaluated for each non-trivial decision in the
bootstrap plan, with the chosen option and the rationale. Each entry is a mini ADR; when
a future change reverses a decision, replace the entry with a link to a proper ADR under
`docs/adr/`.

---

## R-001 — Python dependency manager

**Question**: How do we manage Python dependencies and lock them reproducibly?

**Options evaluated**:

| Option | Pros | Cons |
|---|---|---|
| `pip` + `requirements.txt` | Universal, zero learning curve | No lockfile semantics, no resolver guarantees, slow |
| `poetry` | Mature, good lockfile | Slow installs, opinionated layout, virtualenv-in-project quirks on CI |
| **`uv` (chosen)** | 10-30× faster than poetry, official Astral support, drop-in `pyproject.toml`, `uv.lock` is reproducible, ships its own Python distributions if needed | Newer (2024+); minor ecosystem gaps with old PyPI packages |
| `pdm` | Similar to poetry, PEP 582 support | Smaller community, less mindshare in 2026 |

**Decision**: **`uv`**. Cold install speed and lockfile reproducibility matter most for
a solo dev who clones the repo on different machines. Astral has shown commitment.

**Trigger to revisit**: any sustained `uv sync` failure on a transitive dependency that
poetry handles.

---

## R-002 — Web framework for the PWA

**Question**: Which web framework should the PWA use?

**Options evaluated**:

| Option | Pros | Cons |
|---|---|---|
| React 18 + Vite | Largest ecosystem, lots of PWA recipes | Largest bundle (~45 KB gz baseline), more boilerplate |
| Vue 3 + Vite | Smaller than React, gentle learning curve | Smaller PWA tooling ecosystem |
| **Svelte 5 (chosen)** | Smallest bundle (~10-15 KB gz baseline), compiler-first; runes (`$state`/`$derived`/`$effect`) give explicit reactivity with materially better TS inference; ~10% smaller runtime than Svelte 4 | Smaller community than React; some libraries still ship Svelte-4-only wrappers |
| Svelte 4 + Vite | Stable, larger lib coverage today | Choosing it forces a future migration to v5 with no upside in a greenfield app |
| SolidJS | Very small, signal-based | Even smaller community, fewer PWA examples |
| Vanilla TS + lit-html | No framework overhead | More manual work for routing, store, etc. |

**Decision**: **Svelte 5 + Vite + `vite-plugin-pwa`**.

**Rationale**: bundle size directly affects mobile first-paint, the critical UX moment
at 12:00 PM premiere. As of 2026-06, Svelte 5 has been GA for ~18 months — the "still
settling" concern that applied in late 2024 no longer holds. Runes are now the documented
default and TS inference is significantly better than Svelte 4, which matters for a solo
maintainer who can't afford "why isn't this updating?" debugging sessions. The only
realistic downside is a missing v5 wrapper for some niche library; if that bites, R-B5
in `plan.md` documents the rollback to Svelte 4 — a zero-API-change downgrade given the
placeholder scope of this module.

**Trigger to revisit**: a critical PWA library (`vite-plugin-pwa`, workbox bridge,
or the eventual i18n lib) ships an incompatibility that has no v5 patch within 2 weeks.

---

## R-003 — Local task runner (revised 2026-06-07 by PO)

**Question**: What runs the developer's daily commands (install, dev, test)?

**Options evaluated**:

| Option | Pros | Cons |
|---|---|---|
| `make` | Universal on Linux/macOS | Tabs/spaces traps, weak on Windows, primitive arg handling |
| `just` | Cross-platform, native arg parsing, recipe deps, dotenv support | Extra prerequisite binary on every contributor machine |
| **pnpm scripts via root `package.json` (chosen)** | Zero extra prereq (pnpm is already required for the web), cross-platform identically, workspace-aware, `concurrently` handles parallel API+web boot, every Node/web dev already knows the conventions | Awkward for Python-only contributors who would otherwise skip Node; cross-language orchestration relies on short shell snippets |
| `task` (Taskfile.dev) | Cross-platform, YAML | YAML for shell commands is awkward |

**Decision**: **pnpm scripts** in the root `package.json`, with `pnpm-workspace.yaml`
declaring `apps/web` (and a thin `package.json` in `apps/api` only to host the few
delegation scripts that wrap `uv run …`).

**Rationale (PO decision, 2026-06-07)**: avoiding a separate task-runner binary
simplifies onboarding. The polyglot delegation pattern is:

- `pnpm install` → recursive workspace install + post-install hook that runs
  `cd apps/api && uv sync --frozen`.
- `pnpm dev` → `pnpm db:up && pnpm migrate && concurrently --kill-others-on-fail
  "pnpm --filter ./apps/api dev" "pnpm --filter ./apps/web dev"`.
- `pnpm test` → `pnpm --filter ./apps/api test && pnpm --filter ./apps/web test`.
- Python-only recipes (`migrate`, `test:api`, `check:api`, `format:api`) all do
  `pnpm --filter ./apps/api <script>`, which delegates to a `package.json` whose
  script body is `uv run alembic …` / `uv run pytest …` / `uv run ruff …`.

**Trigger to revisit**: if the post-install hook becomes flaky on CI runners or the
`concurrently` invocation produces interleaved logs that hurt debugging, evaluate
`mprocs` or `overmind` (both single-binary, both keep the rest of the design intact).

---

## R-004 — Postgres provider for production

**Question**: Where does Postgres live in production?

**Options evaluated**:

| Option | Free tier | Branching | Async driver | Verdict |
|---|---|---|---|---|
| **Neon (chosen)** | 0.5 GB + PITR 7d + branching | Yes (great for preview envs) | Excellent (asyncpg ok) | Win |
| Supabase | 0.5 GB + auth + storage bundled | Limited | OK | Auth bundle not needed; we use our own JWT |
| Railway PG | Tier ended in 2024; now paid | No | OK | Violates Gate 1 |
| Render PG | Free 90-day expiry | No | OK | Self-deletes after 90 days |
| Fly Postgres | Free tier via Tigris | No managed branching | OK | More ops burden than Neon |

**Decision**: **Neon**. Branching enables preview deploys for `tick-*` workflow testing
without polluting the main DB.

---

## R-005 — Asset hosting (R2 vs S3 vs Backblaze)

**Question**: Where do generated panel images and TTS files live?

**Options evaluated**:

| Option | Free tier | Egress | Verdict |
|---|---|---|---|
| **Cloudflare R2 (chosen)** | 10 GB storage + 1M Class A + 10M Class B ops/mo | $0 egress | Win, zero-egress is decisive |
| AWS S3 | 5 GB free for 12 months | $0.09/GB egress after free tier | Egress cost at 12:00 PM premieres is the risk |
| Backblaze B2 | 10 GB free | $0.01/GB egress | Cheaper than S3 but not free |

**Decision**: **R2**. The 12:00 PM premiere is a burst of cold reads; R2's zero egress
eliminates the only realistic surprise bill vector.

---

## R-006 — Cron orchestration

**Question**: What triggers the FSM transitions at 12:00 / 18:00 / 23:00 ART?

**Options evaluated**:

| Option | Pros | Cons |
|---|---|---|
| `APScheduler` in-process on Fly | Simple, no external dep | If Fly suspends the VM, schedule misses fire; restart cold-starts may shift the trigger |
| Fly.io scheduled machines | First-class, easy | Free-tier machine-hours risk; less observable than GH |
| **GitHub Actions cron (chosen)** | Free, observable, retries, version-controlled, audit log | Documented jitter up to 15 min in pop pop windows; mitigated by watchdog tick at 23:55 |
| Cloudflare Workers Cron Triggers | Reliable, free | Workers can't easily talk to Fly endpoints with HMAC body construction without overhead |

**Decision**: **GitHub Actions cron + HMAC**. The audit trail and PR-versionable cron
definitions outweigh the jitter risk; the 23:55 watchdog catches any miss.

---

## R-007 — Testing strategy for ephemeral Postgres

**Question**: How do integration tests get a clean Postgres?

**Options evaluated**:

| Option | Pros | Cons |
|---|---|---|
| `testcontainers-python` | Hermetic, per-test isolation | Slow on Windows runners; Docker-in-Docker on some CI envs |
| `pytest-postgresql` | Pure Python, fast | Requires a real `postgresql` binary; less isolation |
| **CI service container + local docker-compose (chosen hybrid)** | Fastest CI; matches dev exactly | Two configurations to maintain |
| In-memory SQLite | Fastest | Diverges from PG semantics (advisory locks, JSON ops, TZ) — unacceptable |

**Decision**: **Hybrid**. CI uses a `services: postgres:16` block; local dev uses
docker-compose. Both expose `DATABASE_URL` the same way. `testcontainers` deferred
to a future ADR if hermetic per-test isolation becomes needed.

---

## R-008 — HMAC for `/internal/transition`

**Question**: How do we authenticate cron requests without a full secrets-management
system?

**Options evaluated**:

| Option | Pros | Cons |
|---|---|---|
| **HMAC-SHA256 over body with `TICK_SECRET` (chosen)** | Stateless, fast, no DB hit, replay-protected by timestamp | Single shared secret (acceptable for solo-dev MVP) |
| JWT signed by GH Actions OIDC | More auditable per-workflow | Requires Fly to validate GH OIDC; setup heavy |
| IP allow-list | Simple | GH Actions IPs change; would whitelist all of GH |
| mTLS | Most rigorous | Wildly overkill for MVP |

**Decision**: **HMAC-SHA256**. Timestamp tolerance of ±300 s; `trigger_id` UNIQUE in
`state_transitions` (module 003) prevents replay even within the tolerance window.

---

## R-009 — Why ship `tick-*` workflows disabled

**Question**: Why include the four cron workflows in module 001 instead of waiting
for module 003?

**Reasoning**:

1. **Single PR for ops surface**: developers can review the cron schedule, HMAC payload
   format, and secret layout in one place, once.
2. **No accidental fires**: with `schedule:` commented out and only `workflow_dispatch`
   enabled, no scheduled run can hit the stub endpoint.
3. **Drives the stub's correctness**: writing the workflow forces the HMAC stub to be
   end-to-end testable via `workflow_dispatch` from day 1.
4. **Bus factor**: if module 003 stalls, the cron skeleton is already merged and
   reviewed.

**Trigger to revisit**: never; this is a one-shot convenience. Module 003 simply flips
the `schedule:` block back on.

---

## R-010 — Logging library

**Question**: stdlib `logging`, `loguru`, or `structlog`?

**Decision**: **`structlog`**. JSON-by-default output makes Fly logs greppable; native
context-binding (`bind(request_id=...)`) is exactly what we need for cycle-id
correlation in future modules. `loguru` was the runner-up but its global-singleton
default fights testability.

---

## R-011 — JWT library (forward-looking)

Although JWT issuance lands in module 002, we pre-select the library here so module 001
can install it as a transitive dep (the HMAC middleware shares utilities):

**Decision**: **`PyJWT ~=2.9`**. Smallest, most-vetted, supports HS256 (our chosen
algorithm). `python-jose` was an alternative but has a less active maintenance pulse.

---

## Open items (carried to follow-up modules)

- **OQ-RES-1**: When the first real cycle ships (module 003), confirm that GH Actions
  cron jitter at 12:00 ART (15:00 UTC) is within tolerance. Plan B: pre-emptive trigger
  at `11:55` from a Cloudflare Worker free tier.
- **OQ-RES-2**: Module 011 will need a VAPID private key generation script — designate
  whether it's committed (encrypted) or runtime-provisioned.
