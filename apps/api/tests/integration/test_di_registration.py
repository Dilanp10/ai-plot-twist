"""Integration: FastAPI lifespan wires the real director_filter (T-010).

Drives :func:`app.main.create_app` through its lifespan three ways:
  - Both LLM provider keys present → real router + real side-effect.
  - Neither key → stub stays.
  - Single key → degraded single-provider router.

We invoke the lifespan context manager directly (rather than going
through httpx.ASGITransport) so the test doesn't open a DB connection
that asyncpg can't clean up when the loop closes.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.domain import side_effects


@pytest.fixture(autouse=True)
def _reset_registry() -> Iterator[None]:
    """Restore the stub in the side_effects registry after each test.

    The lifespan mutates a module-global registry; without this fixture
    a successful T-010 wiring would leak into the next test.
    """
    from app.domain.side_effects import director_filter_stub

    yield
    side_effects.register("director_filter", director_filter_stub)


async def _drive_lifespan(app: Any) -> dict[str, Any]:
    """Run the FastAPI lifespan and return ``app.state`` snapshot.

    Calls the Starlette lifespan context directly. No HTTP request is
    issued, so the lazy DB engine never opens a connection.
    """
    snapshot: dict[str, Any] = {}
    async with app.router.lifespan_context(app):
        snapshot["director_router"] = getattr(
            app.state, "director_router", "MISSING"
        )
        snapshot["registered_side_effect"] = side_effects.get(
            "director_filter"
        )
    return snapshot


# ---------------------------------------------------------------------------
# Helpers to build the app cleanly per test
# ---------------------------------------------------------------------------


def _build_app(monkeypatch: pytest.MonkeyPatch, **env: str | None) -> Any:
    """Build the FastAPI app with overridden env vars.

    We delete then set each key so blank strings count as 'unset'.
    """
    from app.settings import get_settings

    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    get_settings.cache_clear()

    from app.main import create_app

    return create_app()


# ---------------------------------------------------------------------------
# 1. Both keys present → real router + real side-effect registered
# ---------------------------------------------------------------------------


async def test_lifespan_registers_real_filter_when_both_keys_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.side_effects import director_filter_stub
    from app.providers.llm import LLMProviderRouter

    app = _build_app(
        monkeypatch,
        GEMINI_API_KEY="dummy-gemini-key",
        GITHUB_MODELS_TOKEN="dummy-ghm-token",
    )
    snap = await _drive_lifespan(app)

    assert isinstance(snap["director_router"], LLMProviderRouter)
    router = snap["director_router"]
    assert router.provider_names == ("gemini", "github_models")

    # The side-effect registry must point at the real impl, not the stub.
    fn = snap["registered_side_effect"]
    assert fn is not director_filter_stub
    # The real impl is a coroutine function the closure factory returned.
    assert callable(fn)


# ---------------------------------------------------------------------------
# 2. Neither key present → stub stays
# ---------------------------------------------------------------------------


async def test_lifespan_keeps_stub_when_no_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.domain.side_effects import director_filter_stub

    app = _build_app(
        monkeypatch,
        GEMINI_API_KEY=None,
        GITHUB_MODELS_TOKEN=None,
    )
    snap = await _drive_lifespan(app)

    assert snap["director_router"] is None
    assert snap["registered_side_effect"] is director_filter_stub


# ---------------------------------------------------------------------------
# 3. Single key → degraded single-provider router
# ---------------------------------------------------------------------------


async def test_lifespan_builds_single_provider_router_with_only_gemini(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.llm import LLMProviderRouter

    app = _build_app(
        monkeypatch,
        GEMINI_API_KEY="dummy-gemini-key",
        GITHUB_MODELS_TOKEN=None,
    )
    snap = await _drive_lifespan(app)

    router = snap["director_router"]
    assert isinstance(router, LLMProviderRouter)
    assert router.provider_names == ("gemini",)


async def test_lifespan_builds_single_provider_router_with_only_github_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.llm import LLMProviderRouter

    app = _build_app(
        monkeypatch,
        GEMINI_API_KEY=None,
        GITHUB_MODELS_TOKEN="dummy-ghm-token",
    )
    snap = await _drive_lifespan(app)

    router = snap["director_router"]
    assert isinstance(router, LLMProviderRouter)
    assert router.provider_names == ("github_models",)
