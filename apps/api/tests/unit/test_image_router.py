"""Unit tests: ImageProviderRouter.

Module 009 / Task T-005.

Spec tasks.md asks for six specific branches; we cover all of them plus
a few sanity tests around health-checking + chain-exhausted semantics.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.providers.image import (
    ImageProvider,
    ImageProviderError,
    ImageProviderInvalidOutput,
    ImageProviderRateLimited,
    ImageProviderRouter,
    ImageProviderUnavailable,
    ImageRequest,
    ImageResult,
)

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeProvider(ImageProvider):
    """Stub provider whose ``generate`` and ``health`` are configurable."""

    def __init__(
        self,
        name: str,
        generate_side_effect: Callable[[ImageRequest], ImageResult]
        | list[BaseException | ImageResult]
        | BaseException
        | ImageResult,
        *,
        healthy: bool = True,
    ) -> None:
        self.name = name
        self._healthy = healthy
        self._calls = 0
        self._side_effect = generate_side_effect

    async def health(self) -> bool:
        return self._healthy

    async def generate(self, req: ImageRequest) -> ImageResult:
        self._calls += 1
        se = self._side_effect
        if callable(se):
            return se(req)
        item = (
            se[min(self._calls - 1, len(se) - 1)]
            if isinstance(se, list)
            else se
        )
        if isinstance(item, BaseException):
            raise item
        return item

    @property
    def capabilities(self) -> dict[str, Any]:
        return {}

    @property
    def call_count(self) -> int:
        return self._calls


def _result(provider: str) -> ImageResult:
    return ImageResult(
        bytes_=b"\x89PNG",
        mime_type="image/png",
        provider=provider,
        model=f"{provider}:test",
        latency_ms=0,
    )


_REQ = ImageRequest(prompt="x", seed=0)


def _router(*providers: ImageProvider, **kwargs: Any) -> ImageProviderRouter:
    """Build a router with zero-delay backoff so tests stay fast."""
    return ImageProviderRouter(
        providers=providers,
        backoff_schedule_seconds=(0.0, 0.0),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 1. First provider succeeds
# ---------------------------------------------------------------------------


async def test_success_on_first_provider() -> None:
    p1 = _FakeProvider("p1", _result("p1"))
    p2 = _FakeProvider("p2", AssertionError("should not be called"))
    router = _router(p1, p2)

    result = await router.render(_REQ)

    assert result.provider == "p1"
    assert p1.call_count == 1
    assert p2.call_count == 0


# ---------------------------------------------------------------------------
# 2. RateLimited → skip immediately (no retry)
# ---------------------------------------------------------------------------


async def test_rate_limited_skips_to_next() -> None:
    p1 = _FakeProvider("p1", ImageProviderRateLimited("429"))
    p2 = _FakeProvider("p2", _result("p2"))
    router = _router(p1, p2)

    result = await router.render(_REQ)

    assert result.provider == "p2"
    assert p1.call_count == 1  # NO retry on rate-limited
    assert p2.call_count == 1


# ---------------------------------------------------------------------------
# 3. Unavailable retries with backoff then succeeds
# ---------------------------------------------------------------------------


async def test_unavailable_retries_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two Unavailable failures then success → succeed on the same provider."""
    sleep_mock = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", sleep_mock)

    p1 = _FakeProvider(
        "p1",
        [
            ImageProviderUnavailable("503"),
            ImageProviderUnavailable("503 again"),
            _result("p1"),
        ],
    )
    p2 = _FakeProvider("p2", AssertionError("should not be called"))
    router = ImageProviderRouter(
        providers=[p1, p2],
        max_retries_on_unavailable=2,
        backoff_schedule_seconds=(1.0, 3.0),
    )

    result = await router.render(_REQ)

    assert result.provider == "p1"
    assert p1.call_count == 3
    assert p2.call_count == 0
    # Two sleeps, with the documented delays
    assert sleep_mock.await_count == 2
    delays = [call.args[0] for call in sleep_mock.await_args_list]
    assert delays == [1.0, 3.0]


# ---------------------------------------------------------------------------
# 4. Unavailable everywhere → chain exhausted (raises Unavailable)
# ---------------------------------------------------------------------------


async def test_unavailable_all_providers_exhausted() -> None:
    p1 = _FakeProvider("p1", ImageProviderUnavailable("503"))
    p2 = _FakeProvider("p2", ImageProviderUnavailable("503"))
    router = _router(p1, p2, max_retries_on_unavailable=1)

    with pytest.raises(ImageProviderUnavailable) as exc:
        await router.render(_REQ)

    assert "exhausted" in str(exc.value).lower()
    # Each provider exhausted its retry budget (initial + 1 retry = 2 calls)
    assert p1.call_count == 2
    assert p2.call_count == 2


# ---------------------------------------------------------------------------
# 5. InvalidOutput → skip without retry
# ---------------------------------------------------------------------------


async def test_invalid_output_skips_no_retry() -> None:
    p1 = _FakeProvider("p1", ImageProviderInvalidOutput("garbage"))
    p2 = _FakeProvider("p2", _result("p2"))
    router = _router(p1, p2)

    result = await router.render(_REQ)

    assert result.provider == "p2"
    assert p1.call_count == 1  # no retries on InvalidOutput
    assert p2.call_count == 1


# ---------------------------------------------------------------------------
# 6. Health=False skips without attempting generate
# ---------------------------------------------------------------------------


async def test_health_false_skips_no_attempt() -> None:
    p1 = _FakeProvider("p1", AssertionError("should never run"), healthy=False)
    p2 = _FakeProvider("p2", _result("p2"))
    router = _router(p1, p2)

    result = await router.render(_REQ)

    assert result.provider == "p2"
    assert p1.call_count == 0
    assert p2.call_count == 1


# ---------------------------------------------------------------------------
# Bonus: empty chain
# ---------------------------------------------------------------------------


async def test_empty_chain_raises_unavailable() -> None:
    router = _router()
    with pytest.raises(ImageProviderUnavailable, match="empty"):
        await router.render(_REQ)


# ---------------------------------------------------------------------------
# Bonus: base ImageProviderError bubbles up (operator-actionable)
# ---------------------------------------------------------------------------


async def test_base_error_propagates_no_failover() -> None:
    """An auth-style error must NOT trigger failover — operator must intervene."""
    p1 = _FakeProvider("p1", ImageProviderError("auth failed"))
    p2 = _FakeProvider("p2", _result("p2"))
    router = _router(p1, p2)

    with pytest.raises(ImageProviderError) as exc:
        await router.render(_REQ)
    # Must NOT be the chain-exhausted Unavailable
    assert not isinstance(exc.value, ImageProviderUnavailable)
    assert p1.call_count == 1
    assert p2.call_count == 0


# ---------------------------------------------------------------------------
# Bonus: check_health=False skips the probe
# ---------------------------------------------------------------------------


async def test_check_health_false_does_not_probe() -> None:
    """When check_health=False, an unhealthy provider is still tried."""
    p1 = _FakeProvider("p1", _result("p1"), healthy=False)
    router = _router(p1, check_health=False)

    result = await router.render(_REQ)

    assert result.provider == "p1"
    assert p1.call_count == 1
