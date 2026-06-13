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

from app.api.auth import router as auth_router
from app.api.chapters import router as chapters_router
from app.api.health import router as health_router
from app.api.internal_director_replay import router as internal_director_replay_router
from app.api.internal_health_cycle import router as internal_health_cycle_router
from app.api.internal_kill_switch import router as internal_kill_switch_router
from app.api.internal_transition import router as internal_transition_router
from app.api.me_twists import router as me_twists_router
from app.api.seasons import router as seasons_router
from app.api.twists import router as twists_router
from app.db import dispose_engine, get_session_factory
from app.domain import side_effects
from app.domain.director_filter import build_director_filter_side_effect
from app.errors import ProblemDetail, problem_handler
from app.logging import configure_logging, get_logger
from app.middleware.request_id import RequestIdMiddleware
from app.providers.llm import (
    GeminiProvider,
    GitHubModelsProvider,
    LLMProvider,
    LLMProviderRouter,
)
from app.settings import Settings, get_settings

_log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan: configure logging on startup, dispose engine on shutdown.

    The DB engine is created lazily on first use (see ``app.db.get_engine``),
    so startup does not depend on the DB being reachable — the ``/healthz``
    endpoint (T-010) is what surfaces DB problems.

    Module 006 / T-010: if both ``GEMINI_API_KEY`` and
    ``GITHUB_MODELS_TOKEN`` are set, wire the real
    :class:`LLMProviderRouter` onto ``app.state.director_router`` and
    overwrite the no-op ``director_filter`` side-effect registered by
    module 003. Missing either key keeps the stub in place and logs a
    warning so the operator can fix the deployment.
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    _log.info(
        "app_startup",
        env=settings.env,
        log_level=settings.log_level,
    )

    # Spec edge case: the app must boot even when TICK_SECRET is missing.
    # The HMAC dependency returns 503 at request time, but we log a single
    # warning at boot so the operator can fix the deployment without
    # waiting for a cron tick.
    if not settings.tick_secret:
        _log.warning(
            "tick_secret_missing_at_boot",
            detail="TICK_SECRET is not set; /internal/* will return 503.",
        )

    _wire_director_filter(app, settings)

    try:
        yield
    finally:
        _log.info("app_shutdown")
        await dispose_engine()


def _build_director_router(
    settings: Settings,
) -> LLMProviderRouter | None:
    """Construct the production LLMProviderRouter from ``settings``.

    Returns ``None`` when neither provider key is set; a single-provider
    router when only one is set (logs a degraded-mode warning); the full
    ``[Gemini, GitHubModels]`` chain when both are set.
    """
    providers: list[LLMProvider] = []
    if settings.gemini_api_key:
        providers.append(GeminiProvider(api_key=settings.gemini_api_key))
    if settings.github_models_token:
        providers.append(
            GitHubModelsProvider(api_key=settings.github_models_token)
        )
    if not providers:
        return None
    return LLMProviderRouter(providers)


def _wire_director_filter(app: FastAPI, settings: Settings) -> None:
    """Register the real director_filter side-effect when keys are present.

    If keys are missing, the no-op stub registered by
    :mod:`app.domain.side_effects` at import time stays in place so the
    FSM still cycles through ``FILTERING → VOTACION`` without invoking
    an LLM (useful for staging without quota).
    """
    router = _build_director_router(settings)
    if router is None:
        _log.warning(
            "director_router_missing_keys",
            detail=(
                "GEMINI_API_KEY and GITHUB_MODELS_TOKEN are both unset; "
                "director_filter remains a no-op stub. The FSM will skip "
                "moderation and twists stay in pending_review."
            ),
        )
        app.state.director_router = None
        return

    if len(router.provider_names) == 1:
        _log.warning(
            "director_router_degraded",
            detail=(
                "Only one LLM provider key is set; the router has no "
                "fallback. Set the missing key to recover full FR-004 "
                "coverage."
            ),
            providers=router.provider_names,
        )
    else:
        _log.info(
            "director_router_registered",
            providers=router.provider_names,
        )

    app.state.director_router = router

    real_side_effect = build_director_filter_side_effect(
        get_session_factory(), router
    )
    side_effects.register("director_filter", real_side_effect)
    _log.info("side_effect_registered", name="director_filter")


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

    # Convert ProblemDetail exceptions into RFC 7807 application/problem+json
    # responses (see app/errors.py).
    app.add_exception_handler(ProblemDetail, problem_handler)

    # Mount routers
    app.include_router(health_router)
    app.include_router(internal_transition_router)
    app.include_router(internal_kill_switch_router)
    app.include_router(internal_health_cycle_router)
    app.include_router(internal_director_replay_router)
    app.include_router(auth_router)
    app.include_router(chapters_router)
    app.include_router(seasons_router)
    app.include_router(twists_router)
    app.include_router(me_twists_router)

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
