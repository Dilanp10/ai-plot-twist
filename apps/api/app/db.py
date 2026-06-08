"""Async database engine and session factory for the AI Plot Twist API.

Provides a lazily-created singleton ``AsyncEngine`` and an
``async_sessionmaker`` backed by it, plus a ``get_session()`` FastAPI
dependency.

The engine is **not** created at import time — it is created on the first call
to ``get_engine()``. This makes it easy for tests to set ``DATABASE_URL``
before the engine is initialized without fighting the import graph.

Typical usage in a route::

    from fastapi import Depends
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.db import get_session

    async def my_route(db: AsyncSession = Depends(get_session)) -> ...:
        result = await db.execute(select(MyModel))
        await db.commit()

Lifecycle::

    # In app lifespan (T-008):
    from app.db import dispose_engine
    await dispose_engine()   # releases all pooled connections on shutdown
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.settings import get_settings

# ---------------------------------------------------------------------------
# Module-level singletons (lazily initialized)
# ---------------------------------------------------------------------------

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the application-wide async engine (created lazily on first call).

    Subsequent calls return the same instance. Thread-safe within asyncio's
    single-threaded event loop; no lock needed.
    """
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            # Ping the connection before returning it from the pool to detect
            # stale connections (e.g. after a Neon cold start).
            pool_pre_ping=True,
            # Conservative pool sizing for Fly.io free tier (256 MB / 1 vCPU).
            pool_size=5,
            max_overflow=10,
            # Echo SQL statements only in dev to avoid log noise in prod/test.
            echo=settings.is_dev,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the async session factory (created lazily, bound to the engine)."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            # Keep model attributes accessible after the session closes without
            # triggering lazy loads.
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _session_factory


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yield an open ``AsyncSession``.

    The session is **not** auto-committed — route handlers are responsible for
    calling ``await db.commit()`` explicitly. This gives routes fine-grained
    control over transaction boundaries.

    On exit (normal or exception), ``AsyncSession.__aexit__`` closes the
    session and implicitly rolls back any pending transaction.

    Usage::

        async def route(db: AsyncSession = Depends(get_session)) -> ...:
            await db.execute(...)
            await db.commit()
    """
    async with get_session_factory()() as session:
        yield session


# ---------------------------------------------------------------------------
# Lifecycle helper
# ---------------------------------------------------------------------------


async def dispose_engine() -> None:
    """Dispose the engine and release all pooled connections.

    Call this in the FastAPI lifespan shutdown hook (implemented in T-008)
    and in test teardown fixtures to avoid "connection still open" warnings.
    """
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
