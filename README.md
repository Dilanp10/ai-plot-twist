# AI Plot Twist

Juego social-narrativo de ciclo diario donde una comunidad cerrada (10–40 personas) co-escribe una serie generada por IA, capítulo a capítulo. Cada 24 horas se libera **un capítulo nuevo** cuya trama está dictada por la propuesta más votada del día anterior.

> **Estado:** MVP en construcción (closed beta). Costo objetivo: **USD 0/mes**.
> **Stack:** FastAPI · Svelte 5 PWA · PostgreSQL 16 · GitHub Actions cron.
> **Zona horaria:** `America/Argentina/Buenos_Aires` (UTC-3, sin DST).

---

## Requisitos previos

Hace falta tener instalado en la máquina:

| Herramienta | Versión | Instalación |
|---|---|---|
| Python | 3.11.x | <https://www.python.org/downloads/> |
| Node | 20.x LTS | <https://nodejs.org/> |
| pnpm | 9.x | `npm install -g pnpm@9` |
| uv | última | `winget install astral-sh.uv` (Win) · `curl -LsSf https://astral.sh/uv/install.sh \| sh` (mac/linux) |
| Docker Desktop | 4.30+ | <https://www.docker.com/> |
| git | 2.40+ | gestor de paquetes del sistema |

No hace falta ningún task runner extra (no `just`, no `make`): todo se orquesta con `pnpm` scripts del `package.json` raíz.

---

## Instalación

```sh
git clone https://github.com/<tu-usuario>/ai-plot-twist.git
cd ai-plot-twist
cp .env.example .env.local      # editá los valores
pnpm install
```

`pnpm install` instala las dependencias de Node (recursivo en el workspace) y dispara un post-install que corre `uv sync --frozen` en `apps/api/` para las de Python.

---

## Desarrollo local

```sh
pnpm dev
```

Esto:

1. Levanta Postgres 16 en Docker (`pnpm db:up`).
2. Corre migraciones de Alembic (`pnpm migrate`).
3. Arranca en paralelo la API FastAPI en `:8000` y la PWA en `:5173`.

Verificación rápida:

```sh
curl http://localhost:8000/healthz
# → {"status":"ok","checks":{"database":"ok"}}
```

Abrí `http://localhost:5173` y vas a ver el placeholder: *"Hola, esto es AI Plot Twist — bootstrap OK"*.

---

## Tests, lint y type-check

```sh
pnpm test            # API (pytest) + web (vitest)
pnpm check           # ruff + mypy --strict + eslint + tsc --noEmit
pnpm format          # ruff format + prettier --write
```

Variantes por lado: `pnpm test:api`, `pnpm test:web`, `pnpm check:api`, `pnpm check:web`.

---

## Deploy

### Backend → Fly.io (región `gru`)

```sh
fly auth login
fly apps create ai-plot-twist
fly secrets set \
  DATABASE_URL="<neon-connection-string>" \
  TICK_SECRET="<random-32-bytes-base64>" \
  JWT_SECRET="<random-32-bytes-base64>"
fly deploy --config infra/fly.toml
```

### Frontend → Cloudflare Pages

Se conecta el repo desde el dashboard de Cloudflare. Build command: `pnpm --filter ./apps/web build`. Output dir: `apps/web/dist`.

Walkthrough completo con troubleshooting: [`specs/001-project-bootstrap/quickstart.md`](./specs/001-project-bootstrap/quickstart.md).

---

## Troubleshooting

| Síntoma | Causa probable | Solución |
|---|---|---|
| `pnpm dev` falla con `port 5433 already in use` | Contenedor de Postgres viejo activo | `pnpm db:down && pnpm db:up` |
| `connection refused: localhost:5433` | Docker Desktop apagado | Arrancar Docker, reintentar |
| `pnpm install` falla en post-install con `uv: command not found` | `uv` no está en el PATH | Instalar `uv` y reabrir la terminal |
| `/healthz` devuelve 503 con `database: error` | Migraciones no aplicadas | `pnpm migrate` y revisar el log de Alembic |
| Vite muestra una página en blanco | Service worker viejo cacheado | DevTools → Application → Unregister SW → hard reload |
| `/api/v1/internal/transition` devuelve 401 | Body o firma HMAC mal armada | Re-encodear sin newline al final; verificar `TICK_SECRET` |
| `/api/v1/internal/transition` devuelve 409 | Desfasaje de reloj > 300 s | Resincronizar el reloj del sistema |

Tabla completa: [`specs/001-project-bootstrap/quickstart.md#troubleshooting`](./specs/001-project-bootstrap/quickstart.md#troubleshooting).

---

## Estructura del repo

```
ai-plot-twist/
├── apps/
│   ├── api/         FastAPI (Python 3.11 + uv + SQLAlchemy 2 async + Alembic)
│   └── web/         Svelte 5 + Vite + vite-plugin-pwa
├── infra/           fly.toml + docker-compose.dev.yml
├── packages/        reservado para shared schemas (módulo 010+)
├── specs/           GitHub Spec Kit — un sub-folder por módulo
├── .specify/        constitution + memoria del proyecto
├── .github/         CI + cron heartbeat (4 workflows tick-*.yml)
├── SDD.md           Software Design Document maestro
└── package.json     orquestador pnpm (raíz)
```

---

## Documentación

- [**SDD.md**](./SDD.md) — Software Design Document maestro (arquitectura, FSM, contratos, riesgos).
- [**.specify/memory/constitution.md**](./.specify/memory/constitution.md) — los 10 gates no-negociables del proyecto.
- [**specs/README.md**](./specs/README.md) — índice de los 11 módulos del MVP.

---

## Licencia

MIT — ver [LICENSE](./LICENSE).
