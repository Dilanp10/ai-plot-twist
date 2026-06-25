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
from app.api.characters import router as characters_router
from app.api.health import router as health_router
from app.api.internal_client_log import router as internal_client_log_router
from app.api.internal_director_replay import router as internal_director_replay_router
from app.api.internal_generation_rerun import router as internal_generation_rerun_router
from app.api.internal_health_cycle import router as internal_health_cycle_router
from app.api.internal_kill_switch import router as internal_kill_switch_router
from app.api.internal_push_test import router as internal_push_test_router
from app.api.internal_transition import router as internal_transition_router
from app.api.me_twists import router as me_twists_router
from app.api.push import router as push_router
from app.api.seasons import router as seasons_router
from app.api.twists import router as twists_router
from app.api.voting import router as voting_router
from app.db import dispose_engine, get_session_factory
from app.domain import side_effects
from app.domain.director_filter import build_director_filter_side_effect
from app.domain.generation_pipeline import build_generation_pipeline_side_effect
from app.domain.push_fanout import build_push_fanout_side_effect
from app.domain.scriptwriter import Scriptwriter
from app.errors import ProblemDetail, problem_handler
from app.infra.r2_uploader import R2Uploader
from app.infra.webpush_sender import WebPushSender
from app.logging import configure_logging, get_logger
from app.middleware.request_id import RequestIdMiddleware
from app.providers.image import ImageProviderRouter, chain_for_env
from app.providers.llm import (
    GeminiProvider,
    GitHubModelsProvider,
    LLMProvider,
    LLMProviderRouter,
)
from app.providers.video import (
    MINIMAL_MP4,
    VideoProviderRouter,
)
from app.providers.video import chain_for_env as video_chain_for_env
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
    _wire_generation_pipeline(app, settings)
    _wire_push_fanout(app, settings)

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


def _load_placeholder_video_bytes(path_str: str) -> bytes:
    """Return the placeholder mp4 bytes, falling back to MINIMAL_MP4 if missing.

    The committed binary lives at ``assets/placeholder.mp4`` (repo-root
    relative in dev, copied to ``/app/assets/`` in the Docker image). If
    the file vanishes we use the in-process 136-byte sentinel rather than
    crashing the boot — a degraded placeholder beats a downed cycle.
    """
    from pathlib import Path

    candidates = [
        Path(path_str),
        Path("/app") / path_str,
        Path(__file__).resolve().parents[3] / path_str,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_bytes()

    _log.warning(
        "placeholder_video_file_missing",
        searched=[str(c) for c in candidates],
        detail="Falling back to MINIMAL_MP4 sentinel (136 bytes, no visible content).",
    )
    return MINIMAL_MP4


def _wire_generation_pipeline(app: FastAPI, settings: Settings) -> None:
    """Register the real generation_pipeline side-effect when fully configured.

    The pipeline requires three independent capabilities:

    * an LLM router (already built for the director filter — we reuse
      :attr:`app.state.director_router`),
    * an :class:`ImageProviderRouter` via :func:`chain_for_env`, and
    * an :class:`R2Uploader` (needs all four R2 credentials + the public
      base URL).

    If any of these are missing the no-op stub registered by
    :mod:`app.domain.side_effects` stays in place so the FSM still
    cycles through ``GENERACION → PENDING_RELEASE`` (useful for staging
    without quotas). When ``generation_image_chain_env='mvp'`` is set,
    :func:`chain_for_env` will raise ``ValueError`` if the HuggingFace
    token is absent — we catch that and degrade to the stub rather than
    crash the boot.
    """
    app.state.image_router = None
    app.state.scriptwriter = None
    app.state.r2_uploader = None
    app.state.video_router = None
    app.state.placeholder_video_url = None
    app.state.placeholder_video_bytes = None

    director_router = getattr(app.state, "director_router", None)
    if director_router is None:
        _log.warning(
            "generation_pipeline_missing_llm_router",
            detail=(
                "director_router is None (LLM keys absent); "
                "generation_pipeline remains a no-op stub."
            ),
        )
        return

    missing_r2 = [
        name
        for name, value in (
            ("R2_ACCOUNT_ID", settings.r2_account_id),
            ("R2_ACCESS_KEY_ID", settings.r2_access_key_id),
            ("R2_SECRET_ACCESS_KEY", settings.r2_secret_access_key),
            ("R2_BUCKET", settings.r2_bucket),
            ("R2_PUBLIC_BASE_URL", settings.r2_public_base_url),
            ("GENERATION_PLACEHOLDER_URL", settings.generation_placeholder_url),
        )
        if not value
    ]
    if missing_r2:
        _log.warning(
            "generation_pipeline_missing_r2_config",
            missing=missing_r2,
            detail=(
                "R2 / placeholder configuration is incomplete; "
                "generation_pipeline remains a no-op stub."
            ),
        )
        return

    try:
        image_chain = chain_for_env(
            settings.generation_image_chain_env,
            huggingface_token=settings.huggingface_token,
        )
    except (ValueError, NotImplementedError) as exc:
        _log.warning(
            "generation_pipeline_image_chain_failed",
            env=settings.generation_image_chain_env,
            error=str(exc),
        )
        return

    image_router = ImageProviderRouter(
        image_chain,
        max_retries_on_unavailable=settings.t2i_max_retries,
        backoff_schedule_seconds=settings.t2i_backoff_seconds,
    )
    app.state.image_router = image_router

    # mypy-narrowed locals: all five strings are non-empty per the missing_r2
    # check above; pydantic-settings still types them as ``str | None``.
    assert settings.r2_account_id is not None
    assert settings.r2_access_key_id is not None
    assert settings.r2_secret_access_key is not None
    assert settings.r2_bucket is not None
    assert settings.r2_public_base_url is not None
    assert settings.generation_placeholder_url is not None

    uploader = R2Uploader(
        account_id=settings.r2_account_id,
        key_id=settings.r2_access_key_id,
        secret=settings.r2_secret_access_key,
        bucket=settings.r2_bucket,
        public_base_url=settings.r2_public_base_url,
    )

    scriptwriter = Scriptwriter(director_router)
    app.state.scriptwriter = scriptwriter
    app.state.r2_uploader = uploader

    # -------------------------------------------------------------------------
    # T2V wiring (optional — falls back to T2I if anything is missing)
    # -------------------------------------------------------------------------
    video_router: VideoProviderRouter | None = None
    placeholder_video_url: str | None = None
    placeholder_video_bytes: bytes | None = None

    if settings.video_pipeline_enabled and settings.generation_placeholder_video_url:
        try:
            video_chain = video_chain_for_env(
                settings.generation_video_chain_env,
                huggingface_token=settings.huggingface_token,
            )
        except (ValueError, NotImplementedError) as exc:
            _log.warning(
                "generation_pipeline_video_chain_failed",
                env=settings.generation_video_chain_env,
                error=str(exc),
                detail="T2V disabled; coordinator will run T2I directly.",
            )
        else:
            video_router = VideoProviderRouter(
                providers=video_chain,
                max_retries_on_unavailable=settings.t2v_max_retries,
                backoff_schedule_seconds=settings.t2v_backoff_seconds,
            )
            placeholder_video_url = settings.generation_placeholder_video_url
            placeholder_video_bytes = _load_placeholder_video_bytes(
                settings.generation_placeholder_video_path
            )

    app.state.video_router = video_router
    app.state.placeholder_video_url = placeholder_video_url
    app.state.placeholder_video_bytes = placeholder_video_bytes

    real_side_effect = build_generation_pipeline_side_effect(
        get_session_factory(),
        scriptwriter,
        image_router,
        uploader,
        placeholder_url=settings.generation_placeholder_url,
        tts_voice=settings.generation_tts_voice,
        panel_concurrency=settings.generation_panel_concurrency,
        deadline_s=settings.generation_deadline_s,
        video_router=video_router,
        placeholder_video_url=placeholder_video_url,
        placeholder_video_bytes=placeholder_video_bytes,
        clip_concurrency=settings.generation_clip_concurrency,
        clip_duration_s=settings.generation_clip_duration_s,
        video_pipeline_enabled=settings.video_pipeline_enabled,
    )
    side_effects.register("generation_pipeline", real_side_effect)
    _log.info(
        "side_effect_registered",
        name="generation_pipeline",
        image_chain=settings.generation_image_chain_env,
        image_providers=image_router.provider_names,
        video_chain=settings.generation_video_chain_env if video_router else None,
        video_providers=video_router.provider_names if video_router else None,
        t2v_active=video_router is not None,
    )


def _wire_push_fanout(app: FastAPI, settings: Settings) -> None:
    """Register the real push_fanout side-effect when VAPID keys are present.

    If keys are missing the no-op stub registered by
    :mod:`app.domain.push_fanout` stays in place so the FSM still
    transitions through ESTRENO without sending any pushes (useful for
    staging without VAPID credentials).
    """
    if not settings.vapid_private_key or not settings.vapid_public_key:
        _log.warning(
            "push_fanout_missing_vapid_keys",
            detail=(
                "VAPID_PRIVATE_KEY or VAPID_PUBLIC_KEY is not set; "
                "push_fanout remains a no-op stub. "
                "Run `pnpm generate-vapid` and set the keys to enable Web Push."
            ),
        )
        return

    sender = WebPushSender(
        vapid_private_key=settings.vapid_private_key,
        vapid_subject=settings.vapid_subject,
    )
    app.state.push_sender = sender

    real_side_effect = build_push_fanout_side_effect(
        get_session_factory(),
        sender,
        timeout_s=settings.push_fanout_timeout_s,
        threshold=settings.push_failure_threshold,
        concurrency=settings.push_fanout_concurrency,
    )
    side_effects.register("push_fanout", real_side_effect)
    _log.info("side_effect_registered", name="push_fanout")


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
    app.include_router(internal_generation_rerun_router)
    app.include_router(internal_client_log_router)
    app.include_router(internal_push_test_router)
    app.include_router(auth_router)
    app.include_router(push_router)
    app.include_router(chapters_router)
    app.include_router(characters_router)
    app.include_router(seasons_router)
    app.include_router(twists_router)
    app.include_router(me_twists_router)
    app.include_router(voting_router)

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
