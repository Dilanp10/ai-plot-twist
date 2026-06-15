# AI Plot Twist — Deploy Guide

End-to-end guide para llevar el MVP a producción. Cubre:

1. **Backend** → Fly.io (`gru`, São Paulo)
2. **Frontend** → Cloudflare Pages
3. **GitHub Actions** → cron ticks del FSM
4. **Smoke real** → Android device

Estado al 2026-06-15: los 11 módulos cerrados. Falta este deploy y el smoke.

---

## 0. Pre-requisitos

| Recurso | Cómo conseguirlo |
|---|---|
| Cuenta Fly.io | `fly auth signup` |
| Cuenta Neon (Postgres) | https://console.neon.tech |
| Cuenta Cloudflare (Pages + R2) | https://dash.cloudflare.com |
| Bot Discord (alertas) | Server Settings → Integrations → Webhooks |
| API key Gemini | https://aistudio.google.com/apikey |
| Token GitHub Models | https://github.com/settings/tokens (fine-grained, `models:read`) |
| Token HuggingFace | https://huggingface.co/settings/tokens (read) |
| VAPID keys (push) | `pnpm generate-vapid` (genera + imprime ambas) |

Todas las claves van como secrets — nunca en el repo.

---

## 1. Backend → Fly.io

### 1.1 Setup inicial (una sola vez)

```sh
fly auth login
fly apps create ai-plot-twist
```

### 1.2 Setear secrets

Generá los hashes con `openssl rand -base64 32` y completá. **Todos los valores marcados `<...>` son tuyos.**

```sh
fly secrets set \
  DATABASE_URL="<neon-connection-string-asyncpg>" \
  JWT_SECRET="<random-32-bytes-base64>" \
  TICK_SECRET="<random-32-bytes-base64>" \
  ADMIN_TOKEN="<random-32-bytes-base64>" \
  DISCORD_WEBHOOK_URL="<discord-webhook-url>" \
  --config infra/fly.toml

fly secrets set \
  R2_ACCOUNT_ID="<cloudflare-account-id>" \
  R2_ACCESS_KEY_ID="<r2-key-id>" \
  R2_SECRET_ACCESS_KEY="<r2-secret>" \
  R2_BUCKET="ai-plot-twist-assets" \
  R2_PUBLIC_BASE_URL="https://assets.aiplottwist.example" \
  --config infra/fly.toml

fly secrets set \
  GEMINI_API_KEY="<gemini-key>" \
  GITHUB_MODELS_TOKEN="<github-models-pat>" \
  HUGGINGFACE_TOKEN="<hf-token>" \
  GENERATION_IMAGE_CHAIN_ENV="mvp" \
  GENERATION_PLACEHOLDER_URL="https://assets.aiplottwist.example/static/placeholder.webp" \
  --config infra/fly.toml

fly secrets set \
  VAPID_PRIVATE_KEY="<from-pnpm-generate-vapid>" \
  VAPID_PUBLIC_KEY="<from-pnpm-generate-vapid>" \
  VAPID_SUBJECT="mailto:dilanperea10@gmail.com" \
  --config infra/fly.toml
```

> Lista completa de vars: ver [`.env.example`](../.env.example).

### 1.3 Aplicar migraciones contra Neon

Desde local, apuntando a la DB de prod:

```sh
DATABASE_URL="<neon-conn>" uv run --project apps/api alembic upgrade head
```

### 1.4 Subir el asset placeholder a R2

```sh
uv run --project apps/api python apps/api/scripts/upload_static_assets.py
```

### 1.5 Deploy

```sh
fly deploy --config infra/fly.toml
```

### 1.6 Smoke backend

```sh
curl https://ai-plot-twist.fly.dev/healthz
# → {"status":"ok","database":"ok"}
```

Logs en vivo:

```sh
fly logs --config infra/fly.toml
```

Buscá los siguientes log lines al startup:

- `side_effect_registered name=director_filter` (módulo 006 wired)
- `side_effect_registered name=generation_pipeline` (módulo 008 wired)
- `side_effect_registered name=push_fanout` (módulo 011 wired)

Si alguno emite `*_missing_*` significa que faltan secrets — revisar la sección 1.2.

---

## 2. Frontend → Cloudflare Pages

### 2.1 Crear el proyecto

Dashboard Cloudflare → **Pages** → **Create a project** → conectar el repo de GitHub.

**Build configuration:**

| Campo | Valor |
|---|---|
| Production branch | `main` |
| Build command | `pnpm install --frozen-lockfile && pnpm --filter ./apps/web build` |
| Build output directory | `apps/web/dist` |
| Root directory | (vacío — repo root) |
| Node version | `20` |

### 2.2 Verificar headers + redirects

Confirmá que tras el build estos archivos están en `apps/web/dist/`:

- `_headers` — CSP, HSTS, cache rules
- `_redirects` — proxy `/api/*` → `ai-plot-twist.fly.dev`, SPA fallback

Son archivos staticos en `apps/web/public/` que Pages lee automáticamente.

### 2.3 Deploy

El primer deploy se dispara al conectar el repo. Subsecuentes salen automáticamente en cada push a `main`.

### 2.4 Smoke frontend

```sh
# Sustituir <pages-url> por el dominio asignado por Cloudflare
# (e.g. ai-plot-twist.pages.dev).
curl https://<pages-url>/api/v1/healthz
# → debe forwardear a Fly y devolver {"status":"ok",...}
```

Abrir `https://<pages-url>` en Chrome desktop:

1. Onboarding pide código de invitación → introducir uno generado con `pnpm issue-invite`
2. App shell se monta, bottom nav visible
3. DevTools → Application → Service Workers: SW activo
4. DevTools → Lighthouse → PWA score ≥ 90

---

## 3. GitHub Actions → cron del FSM

Los 5 workflows en `.github/workflows/tick-*.yml` necesitan dos secrets en el repo:

```
TICK_SECRET   ← el mismo valor que en Fly.io
API_BASE_URL  ← https://ai-plot-twist.fly.dev
```

Setearlos en **Settings → Secrets and variables → Actions**.

Verificación: trigger manual del workflow `manual-transition.yml` con `to_state=RECEPCION_IDEAS` y mirar que el job termine OK.

---

## 4. Smoke real device (Android)

Sigue la sección §10 de [`specs/011-web-push/quickstart.md`](../specs/011-web-push/quickstart.md):

1. Abrir `https://<pages-url>` en Chrome del Android
2. **Add to Home Screen** desde el InstallPromptCard
3. Abrir la PWA desde el ícono del launcher
4. Onboarding con código nuevo (`pnpm issue-invite` para generarlo)
5. Settings → activar notificaciones (debe pedir permiso)
6. Desde otra máquina:
   ```sh
   curl -X POST https://ai-plot-twist.fly.dev/api/v1/internal/push/test \
     -H "Authorization: Bearer $ADMIN_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{}'
   ```
7. Notificación debe llegar al Android en < 30 s
8. Tap → abre la PWA en `/`

Pass: 1–8 sin fricción.

---

## 5. Observación del primer ciclo (08 T-015)

El primer ciclo de generación corre a las 23:00 ART. Mirar:

```sh
fly logs --config infra/fly.toml | grep -E "winner_picked|scriptwriter_done|panel_render|generation_completed"
```

Cierre exitoso del ciclo:

- `winner_picked` con vote_count > 0
- `scriptwriter_done panels=3..4`
- `panel_render_done` × N (N = panels)
- `generation_completed status=ready` (o `ready_degraded` si algún panel falló)
- `transition VOTACION→PENDING_RELEASE` por side-effect

A las 12:00 del día siguiente: `transition PENDING_RELEASE→ESTRENO` y `push_fanout` se dispara automáticamente.

---

## Troubleshooting rápido

| Síntoma | Probable causa | Acción |
|---|---|---|
| `fly deploy` falla con `dockerfile not found` | corriste el cmd desde `infra/` | volvé al repo root |
| `/healthz` devuelve 503 `database: error` | migraciones no aplicadas a Neon | sección 1.3 |
| Pages build falla en `pnpm install` | Node version mismatch | setear Node 20 en config |
| `/api/v1/...` desde Pages devuelve 404 | falta `_redirects` o build no lo incluyó | verificar `apps/web/dist/_redirects` |
| Push no llega al Android | VAPID keys distintas en backend vs subscribe | confirmar que `/api/v1/push/public-key` devuelve la **misma** key que está en secrets de Fly |
| Lighthouse PWA score < 90 | manifest icons missing / SW no registrado | DevTools → Application → Manifest |
| `generation_pipeline_missing_*` en logs | falta R2 / HuggingFace / placeholder URL | sección 1.2 |

Tabla extendida: cada `specs/NNN-*/quickstart.md` tiene su sección Troubleshooting propia.
