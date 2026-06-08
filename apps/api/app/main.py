"""FastAPI application factory for the AI Plot Twist API.

The app is constructed via ``create_app()`` (not at import time) so tests can
build fresh instances with overridden settings. A module-level ``app`` is
exported as the uvicorn entry point — invoking ``create_app()`` once is
intentional and only touches env loading.

Local dev::

    uv run uvicorn app.main:app --reload --port 8000

Tests::

    from fastapi.testclient import TestClient
    from app.main import create_app
    client = TestClient(create_app())
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import dispose_engine
from app.logging import configure_logging, get_logger
from app.middleware.request_id import RequestIdMiddleware
from app.settings import Settings, get_settings

_log = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan: configure logging on startup, dispose engine on shutdown.

    The DB engine is created lazily on first use (see ``app.db.get_engine``),
    so startup does not depend on the DB being reachable — the ``/healthz``
    endpoint (T-010) is what surfaces DB problems.
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    _log.info(
        "app_startup",
        env=settings.env,
        log_level=settings.log_level,
    )
    try:
        yield
    finally:
        _log.info("app_shutdown")
        await dispose_engine()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and return a configured FastAPI application.

    Args:
        settings: Override settings (for tests). Defaults to the cached
            singleton from ``get_settings()``.
    """
    if settings is None:
        settings = get_settings()

    # Interactive docs are only mounted in dev/test to keep the prod surface
    # minimal. The machine-readable OpenAPI JSON is always available — modules
    # 002+ depend on consumers fetching it from CI.
    docs_enabled = settings.env != "prod"

    app = FastAPI(
        title="AI Plot Twist API",
        version="0.1.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Inject a uuid4 request_id per request, exposed as X-Request-Id and bound
    # to structlog's contextvars for every log entry within the request scope.
    app.add_middleware(RequestIdMiddleware)

    return app


# ---------------------------------------------------------------------------
# Lazy uvicorn entry point
# ---------------------------------------------------------------------------
#
# Uvicorn imports this module and then does ``getattr(module, "app")`` to get
# the ASGI application. We do NOT eagerly call ``create_app()`` at module
# scope: that would force ``get_settings()`` to validate env vars at *import*
# time, which breaks pytest collection on machines without DATABASE_URL set.
#
# PEP 562 ``__getattr__`` resolves this: the app is built the first time
# ``module.app`` is accessed (typically by uvicorn), and cached thereafter.
# Tests import ``create_app`` directly and never touch ``module.app``, so
# their collection stays hermetic.

_app_singleton: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:
    """Module-level ``__getattr__`` (PEP 562) for lazy uvicorn discovery."""
    global _app_singleton
    if name == "app":
        if _app_singleton is None:
            _app_singleton = create_app()
        return _app_singleton
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
