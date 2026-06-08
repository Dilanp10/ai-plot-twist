# =============================================================================
# AI Plot Twist — API runtime image
# =============================================================================
#
# Build from the repo root (build context = cwd when running `fly deploy`):
#
#   fly deploy --config infra/fly.toml
#
# Local smoke test (also run from repo root):
#
#   docker build -f apps/api/Dockerfile -t aiplottwist-api .
#   docker run \
#     -e DATABASE_URL="postgresql+asyncpg://..." \
#     -e JWT_SECRET="secret" \
#     -e TICK_SECRET="secret" \
#     -p 8000:8000 aiplottwist-api
#
# The COPY paths below are relative to the repo root (build context).

FROM python:3.11-slim AS base

# Install uv from the official dist image.
# Using COPY --from keeps the final image slim (no Cargo/curl).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# ── Dependency layer ────────────────────────────────────────────────────────
# Copy only the lockfile artifacts first. Docker caches this layer unless
# pyproject.toml or uv.lock change, saving the full pip-install on every
# source-code-only change.
COPY apps/api/pyproject.toml apps/api/uv.lock ./

# --frozen   : fail if the lockfile is stale (enforces lock integrity at
#              build time — equivalent to pip install --require-hashes).
# --no-dev   : skip devDependencies (pytest, mypy, ruff, stubs, …).
# uv creates .venv inside WORKDIR (/app/.venv).
RUN uv sync --frozen --no-dev

# ── Application layer ────────────────────────────────────────────────────────
COPY apps/api/app/      ./app/
COPY apps/api/alembic/  ./alembic/
COPY apps/api/alembic.ini ./

# ── Runtime config ───────────────────────────────────────────────────────────
# Put .venv/bin on PATH so the CMD can invoke uvicorn directly without
# the `uv run` wrapper (avoids a cold-start sync check in prod).
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENV=prod

EXPOSE 8000

# Single worker: Fly.io free tier is shared-cpu-1x / 256 MB.
# auto_stop_machines = false (fly.toml) keeps the process alive between ticks
# so the advisory-lock logic in module 003+ is not interrupted.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1"]
