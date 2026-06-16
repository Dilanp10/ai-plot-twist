"""Alembic env.py — async engine version.

Reads ``DATABASE_URL`` from ``app.settings`` (kept out of alembic.ini so no
secrets land in version control) and runs migrations through the async
SQLAlchemy engine.

Both ``offline`` (SQL-script generation) and ``online`` (live DB) modes are
supported. ``online`` is the path used by ``alembic upgrade``.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.settings import get_settings

# ---------------------------------------------------------------------------
# Alembic Config: pulled from alembic.ini at runtime.
# ---------------------------------------------------------------------------

config = context.config

# Interpret the config file for Python logging (alembic.ini [loggers] etc.).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the application's DATABASE_URL into the Alembic config UNLESS the
# caller already set one programmatically (e.g. a test that passes a custom
# Config object). Pulling from settings means production never has to put a
# secret in alembic.ini, while tests can still override the URL.
if not config.get_main_option("sqlalchemy.url"):
    # Escape % → %% so configparser's interpolation doesn't choke on
    # URL-encoded passwords (e.g. %23 for #).
    db_url = get_settings().database_url.replace("%", "%%")
    config.set_main_option("sqlalchemy.url", db_url)

# No SQLAlchemy declarative model metadata exists yet. Module 002 will be the
# first to import a shared MetaData object here so autogenerate diffs work.
target_metadata = None


# ---------------------------------------------------------------------------
# Migration runners
# ---------------------------------------------------------------------------


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a live connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Bind Alembic to a live connection and run migrations synchronously.

    Called from ``run_async_migrations`` via ``connection.run_sync(...)``.
    """
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create the async engine and dispatch the sync runner via run_sync()."""
    section = config.get_section(config.config_ini_section) or {}
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online migrations — spins up an event loop."""
    asyncio.run(run_async_migrations())


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
