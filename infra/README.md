# infra/

Infrastructure configuration for AI Plot Twist.

## Archivos

| Archivo | Propósito |
|---|---|
| `docker-compose.dev.yml` | PostgreSQL 16 local para desarrollo. Puerto `5433` (evita conflicto con Postgres del sistema en `:5432`). |
| `fly.toml` | Configuración de deploy a Fly.io (región `gru` — São Paulo). |

## Uso rápido

```sh
# Levantar la base de datos de desarrollo
pnpm db:up

# Apagar el contenedor (conserva el volumen)
pnpm db:down

# Reset completo (borra el volumen y re-migra)
pnpm db:reset

# Correr migraciones de Alembic
pnpm migrate
```

El string de conexión local es:

```
postgresql+asyncpg://app:app@localhost:5433/aiplottwist
```

Copiarlo en `.env.local` → `DATABASE_URL=postgresql+asyncpg://app:app@localhost:5433/aiplottwist`.

## Deploy a Fly.io

```sh
fly deploy --config infra/fly.toml
```

Ejecutar desde la raíz del repo. Fly.io usa el directorio actual como build
context, y el Dockerfile se referencia desde `infra/fly.toml` como
`../apps/api/Dockerfile`.

Antes del primer deploy hay que setear los secrets:

```sh
fly secrets set \
  DATABASE_URL="<neon-connection-string>" \
  TICK_SECRET="$(openssl rand -base64 32)" \
  JWT_SECRET="$(openssl rand -base64 32)"
```
