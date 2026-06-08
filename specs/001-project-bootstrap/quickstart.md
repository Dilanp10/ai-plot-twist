# Quickstart: Project Bootstrap

**Branch**: `001-project-bootstrap` | **Date**: 2026-06-07

End-to-end recipe to clone, install, run, test, and deploy the bootstrap skeleton. If
any step fails, see [Troubleshooting](#troubleshooting).

All commands run from the **repo root** unless noted. Task orchestration uses **pnpm
scripts** defined in the root `package.json` — no `just`, no `make`.

---

## Prerequisites

Install once per machine:

| Tool | Version | Install |
|---|---|---|
| Python | 3.11.x | https://www.python.org/downloads/ (or pyenv) |
| Node | 20.x LTS | https://nodejs.org/ (or fnm/nvm) |
| pnpm | 9.x | `npm install -g pnpm@9` |
| uv | latest | `winget install astral-sh.uv` (Win) / `curl -LsSf https://astral.sh/uv/install.sh \| sh` (mac/linux) |
| Docker Desktop | 4.30+ | https://www.docker.com/ |
| git | 2.40+ | system package manager |

Verify:

```sh
python --version    # Python 3.11.x
node --version      # v20.x.x
pnpm --version      # 9.x.x
uv --version
docker --version
```

> **Note**: there is **no** separate task-runner binary (no `just`, no `make`). Every
> command in this guide is `pnpm <script>`.

---

## 1. Clone and configure

```sh
git clone https://github.com/<you>/ai-plot-twist.git
cd ai-plot-twist
cp .env.example .env.local
```

Open `.env.local` and at minimum set:

```ini
# Required for /internal/transition (HMAC). Any random string works for local dev.
TICK_SECRET=dev-tick-secret-change-me
# Local DB URL (matches docker-compose.dev.yml). Leave as-is.
DATABASE_URL=postgresql+asyncpg://app:app@localhost:5433/aiplottwist
# JWT secret — module 001 doesn't use it yet, but settings.py validates presence.
JWT_SECRET=dev-jwt-secret-change-me
# Used by module 008 (image generation). Empty is fine here.
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET=
```

---

## 2. Install dependencies

```sh
pnpm install
```

What this does:

- `pnpm install --frozen-lockfile` recursively across the workspace (root + `apps/web`).
- A `postinstall` hook runs `cd apps/api && uv sync --frozen` to create `apps/api/.venv`
  from `apps/api/uv.lock`.

Expected: green output, no network errors, ≤ 3 min on a 100 Mbps connection.

> If you want to install only the Python side (e.g., on a server that won't serve web):
> `pnpm install:api` runs the uv step in isolation.

---

## 3. Start the dev stack

```sh
pnpm dev
```

Equivalent shell, for transparency:

```sh
pnpm db:up                                                 # docker compose up -d
pnpm migrate                                               # uv run alembic upgrade head
concurrently --kill-others-on-fail \
  "pnpm --filter ./apps/api dev" \                         # uv run uvicorn app.main:app --reload --port 8000
  "pnpm --filter ./apps/web dev"                           # vite --port 5173
```

In a second terminal, verify:

```sh
curl http://localhost:8000/healthz
# → {"status":"ok","checks":{"database":"ok"}}

# Open the PWA:
start http://localhost:5173     # windows
open http://localhost:5173      # mac
xdg-open http://localhost:5173  # linux
```

The PWA shows a placeholder page: *"Hola, esto es AI Plot Twist — bootstrap OK"*.

DevTools → Application → Manifest must show a valid installable PWA (icons, name,
display: standalone).

---

## 4. Verify the HMAC stub

PowerShell (Windows):

```powershell
$ts = [int][double]::Parse((Get-Date -UFormat %s))
$body = '{"to":"WATCHDOG","ts":' + $ts + ',"trigger_id":"local-test"}'
$secret = 'dev-tick-secret-change-me'
$hmac = New-Object System.Security.Cryptography.HMACSHA256
$hmac.Key = [Text.Encoding]::UTF8.GetBytes($secret)
$sig = [Convert]::ToBase64String($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($body)))
curl.exe -sS -X POST http://localhost:8000/api/v1/internal/transition `
  -H "Content-Type: application/json" `
  -H ("X-Tick-Signature: " + $sig) `
  -d $body
# → {"status":"accepted","noop":true}
```

bash (mac/linux):

```sh
TS=$(date -u +%s)
BODY=$(printf '{"to":"WATCHDOG","ts":%s,"trigger_id":"local-test"}' "$TS")
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "dev-tick-secret-change-me" -binary | base64)

curl -sS -X POST http://localhost:8000/api/v1/internal/transition \
  -H "Content-Type: application/json" \
  -H "X-Tick-Signature: $SIG" \
  -d "$BODY"
# → {"status":"accepted","noop":true}
```

Tamper the body or signature; the response must be `401`. Wait > 5 min and re-send the
same payload; the response must be `409` (timestamp drift).

---

## 5. Run the test suite

```sh
pnpm test           # runs all
pnpm test:api       # python only (uv run pytest)
pnpm test:web       # vitest only
```

Expected: all green; coverage report printed for the API.

---

## 6. Lint and type-check

```sh
pnpm check          # ruff + mypy + eslint + tsc; fails on any error
pnpm format         # ruff format + prettier --write
```

Both API-only and web-only variants exist: `pnpm check:api`, `pnpm check:web`,
`pnpm format:api`, `pnpm format:web`.

---

## 7. First deploy to Fly.io

One-time setup (region `gru` — São Paulo, closest free Fly region to Argentina):

```sh
fly auth login
fly apps create ai-plot-twist            # adjust app name in fly.toml
fly secrets set \
  DATABASE_URL="<neon-connection-string>" \
  TICK_SECRET="<random-32-bytes-base64>" \
  JWT_SECRET="<random-32-bytes-base64>"
```

Deploy:

```sh
fly deploy --config infra/fly.toml
```

Verify:

```sh
curl https://<app>.fly.dev/healthz
# → {"status":"ok","checks":{"database":"ok"}}
```

Trigger the watchdog workflow manually from the GitHub UI
(`Actions → tick-2355-watchdog → Run workflow`); the action log must show
HTTP 202 from `/internal/transition`.

---

## 8. Reset the local DB

```sh
pnpm db:reset
```

What this does: stops the Postgres container, removes its volume, recreates it, and
re-runs migrations. Use any time the dev DB is in a confused state.

---

## Reference: all root scripts

Defined in the root `package.json`:

| Script | What it does |
|---|---|
| `install` | Recursive pnpm install + postinstall uv sync |
| `install:api` | uv sync only |
| `dev` | db:up + migrate + concurrent uvicorn + vite |
| `test` | API + web test suites in series |
| `test:api` / `test:web` | one side only |
| `check` | ruff + mypy + eslint + tsc |
| `check:api` / `check:web` | one side only |
| `format` | ruff format + prettier --write |
| `format:api` / `format:web` | one side only |
| `db:up` | docker compose up -d postgres |
| `db:down` | docker compose down |
| `db:reset` | down + volume rm + up + migrate |
| `migrate` | uv run alembic upgrade head |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `pnpm dev` fails with `port 5433 already in use` | Old Postgres container leftover | `pnpm db:down && pnpm db:up` |
| `connection refused: localhost:5433` | Docker not running | Start Docker Desktop, retry |
| `pnpm install` post-install fails with "uv: command not found" | `uv` is not on the contributor's PATH | Install `uv` per Prerequisites; re-run `pnpm install` |
| `uv sync` fails with SSL error | Corporate proxy | Set `UV_NATIVE_TLS=true` or configure cert |
| Vite shows blank page | Service worker stuck | DevTools → Application → Unregister SW → hard reload |
| `/healthz` returns 503 with `database: error` | DB container started but migrations failed | `pnpm migrate` and inspect the alembic log |
| `/internal/transition` returns 401 | Body or signature mismatch | Re-encode body without trailing newline; ensure `TICK_SECRET` matches |
| `/internal/transition` returns 409 | Timestamp drift | Resync system clock (`w32tm /resync` on Win, `sudo sntp -sS time.apple.com` on mac) |
| `fly deploy` fails on health check | Fly thinks the app isn't healthy in 30 s | Ensure `DATABASE_URL` Fly secret is reachable from `gru` region |
| CI fails on `uv.lock` mismatch | Lockfile not committed | `cd apps/api && uv sync`, commit `uv.lock`, push |
| `concurrently` interleaves logs unreadably | Both processes spamming stdout | Use `pnpm --filter ./apps/api dev` and `pnpm --filter ./apps/web dev` in two terminals |

---

## What's next?

After this module is merged:

- Module **002 (auth-invite-flow)** branches from `main` and adds the first business
  table (`users`), the JWT middleware, and the `/auth/redeem-invite` endpoint.
- Module **003 (cycle-fsm)** flips the `tick-*.yml` workflows from `workflow_dispatch`
  only to scheduled, after implementing real state mutations behind
  `/internal/transition`.
