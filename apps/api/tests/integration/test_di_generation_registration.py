"""Integration: FastAPI lifespan wires the real generation_pipeline (T-011).

Drives :func:`app.main.create_app` through its lifespan in four scenarios:
  - All keys (LLM + R2 + placeholder) present → real side-effect registered.
  - LLM keys absent → stub stays (no LLM = no scriptwriter).
  - LLM ok but R2 keys missing → stub stays.
  - LLM + R2 ok but ``generation_image_chain_env='mvp'`` without HF token
    → stub stays (chain_for_env raises, lifespan degrades).

Same approach as :mod:`test_di_registration` — invoke the Starlette
lifespan context directly to avoid opening a real DB connection.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.domain import side_effects


@pytest.fixture(autouse=True)
def _reset_registry() -> Iterator[None]:
    """Restore both stubs after each test (lifespan mutates a module global)."""
    from app.domain.side_effects import director_filter_stub, generation_pipeline_stub

    yield
    side_effects.register("director_filter", director_filter_stub)
    side_effects.register("generation_pipeline", generation_pipeline_stub)


async def _drive_lifespan(app: Any) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    async with app.router.lifespan_context(app):
        snapshot["image_router"] = getattr(app.state, "image_router", "MISSING")
        snapshot["registered_side_effect"] = side_effects.get("generation_pipeline")
    return snapshot


def _build_app(monkeypatch: pytest.MonkeyPatch, **env: str | None) -> Any:
    from app.settings import get_settings

    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    get_settings.cache_clear()

    from app.main import create_app

    return create_app()


_FULL_CONFIG: dict[str, str | None] = {
    "GEMINI_API_KEY": "dummy-gemini-key",
    "GITHUB_MODELS_TOKEN": "dummy-ghm-token",
    "R2_ACCOUNT_ID": "acct-123",
    "R2_ACCESS_KEY_ID": "key-123",
    "R2_SECRET_ACCESS_KEY": "secret-123",
    "R2_BUCKET": "bucket-123",
    "R2_PUBLIC_BASE_URL": "https://assets.example.com",
    "GENERATION_PLACEHOLDER_URL": "https://assets.example.com/static/placeholder.webp",
    "GENERATION_IMAGE_CHAIN_ENV": "dev",
}


# ---------------------------------------------------------------------------
# 1. Everything present → real side-effect registered
# ---------------------------------------------------------------------------


async def test_lifespan_registers_real_generation_when_fully_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.side_effects import generation_pipeline_stub
    from app.providers.image import ImageProviderRouter

    app = _build_app(monkeypatch, **_FULL_CONFIG)
    snap = await _drive_lifespan(app)

    assert isinstance(snap["image_router"], ImageProviderRouter)
    fn = snap["registered_side_effect"]
    assert fn is not generation_pipeline_stub
    assert callable(fn)


# ---------------------------------------------------------------------------
# 2. LLM keys missing → stub stays (no scriptwriter possible)
# ---------------------------------------------------------------------------


async def test_lifespan_keeps_stub_when_llm_keys_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.side_effects import generation_pipeline_stub

    env = dict(_FULL_CONFIG)
    env["GEMINI_API_KEY"] = None
    env["GITHUB_MODELS_TOKEN"] = None

    app = _build_app(monkeypatch, **env)
    snap = await _drive_lifespan(app)

    assert snap["image_router"] is None
    assert snap["registered_side_effect"] is generation_pipeline_stub


# ---------------------------------------------------------------------------
# 3. R2 missing → stub stays
# ---------------------------------------------------------------------------


async def test_lifespan_keeps_stub_when_r2_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.side_effects import generation_pipeline_stub

    env = dict(_FULL_CONFIG)
    env["R2_BUCKET"] = None  # one missing field is enough

    app = _build_app(monkeypatch, **env)
    snap = await _drive_lifespan(app)

    assert snap["image_router"] is None
    assert snap["registered_side_effect"] is generation_pipeline_stub


# ---------------------------------------------------------------------------
# 4. placeholder URL missing → stub stays
# ---------------------------------------------------------------------------


async def test_lifespan_keeps_stub_when_placeholder_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.side_effects import generation_pipeline_stub

    env = dict(_FULL_CONFIG)
    env["GENERATION_PLACEHOLDER_URL"] = None

    app = _build_app(monkeypatch, **env)
    snap = await _drive_lifespan(app)

    assert snap["image_router"] is None
    assert snap["registered_side_effect"] is generation_pipeline_stub


# ---------------------------------------------------------------------------
# 5. mvp chain without HF token → stub stays (chain_for_env raises)
# ---------------------------------------------------------------------------


async def test_lifespan_keeps_stub_when_mvp_chain_missing_hf_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.side_effects import generation_pipeline_stub

    env = dict(_FULL_CONFIG)
    env["GENERATION_IMAGE_CHAIN_ENV"] = "mvp"
    env["HUGGINGFACE_TOKEN"] = None

    app = _build_app(monkeypatch, **env)
    snap = await _drive_lifespan(app)

    assert snap["image_router"] is None
    assert snap["registered_side_effect"] is generation_pipeline_stub


# ---------------------------------------------------------------------------
# 6. mvp chain WITH HF token → real side-effect registered
# ---------------------------------------------------------------------------


async def test_lifespan_registers_real_generation_with_mvp_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.side_effects import generation_pipeline_stub
    from app.providers.image import ImageProviderRouter

    env = dict(_FULL_CONFIG)
    env["GENERATION_IMAGE_CHAIN_ENV"] = "mvp"
    env["HUGGINGFACE_TOKEN"] = "dummy-hf-token"

    app = _build_app(monkeypatch, **env)
    snap = await _drive_lifespan(app)

    router = snap["image_router"]
    assert isinstance(router, ImageProviderRouter)
    assert router.provider_names == ("pollinations", "hf")
    fn = snap["registered_side_effect"]
    assert fn is not generation_pipeline_stub
