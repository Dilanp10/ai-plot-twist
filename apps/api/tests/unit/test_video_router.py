"""Unit tests: VideoProviderRouter.

Module 012 / Task T-005.

All nine policy branches from FR-005 are covered, plus chain-exhausted and
NotImplementedError propagation.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.providers.video.base import (
    VideoProvider,
    VideoProviderError,
    VideoProviderInvalidOutput,
    VideoProviderRateLimited,
    VideoProviderUnavailable,
    VideoRequest,
    VideoResult,
)
from app.providers.video.fake import MINIMAL_MP4
from app.providers.video.router import VideoProviderRouter

# ---------------------------------------------------------------------------
# Test double
# ---------------------------------------------------------------------------


class _FakeProvider(VideoProvider):
    """Minimal injectable provider for router tests."""

    def __init__(
        self,
        name: str,
        responses: list[VideoResult | BaseException | type[BaseException]],
        *,
        healthy: bool = True,
    ) -> None:
        self.name = name
        self._responses = list(responses)
        self._healthy = healthy
        self._calls = 0

    async def health(self) -> bool:
        return self._healthy

    async def generate(self, req: VideoRequest) -> VideoResult:
        self._calls += 1
        if not self._responses:
            raise VideoProviderUnavailable(f"{self.name}: responses exhausted")
        item = self._responses.pop(0)
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item()
        if isinstance(item, BaseException):
            raise item
        return item

    @property
    def capabilities(self) -> dict[str, Any]:
        return {}

    @property
    def call_count(self) -> int:
        return self._calls


def _result(provider: str = "fake") -> VideoResult:
    return VideoResult(
        bytes_=MINIMAL_MP4,
        mime_type="video/mp4",
        provider=provider,
        model="fake",
        duration_s=5.0,
        frames_count=121,
        latency_ms=0,
    )


_REQ = VideoRequest(prompt="x", seed=0)


def _router(*providers: VideoProvider, **kwargs: Any) -> VideoProviderRouter:
    """Build a router with zero-delay backoff so tests run instantly."""
    return VideoProviderRouter(
        providers=providers,
        backoff_schedule_seconds=(0.0, 0.0, 0.0),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 1. First provider succeeds immediately
# ---------------------------------------------------------------------------


async def test_success_on_first_provider() -> None:
    p1 = _FakeProvider("p1", [_result("p1")])
    p2 = _FakeProvider("p2", [AssertionError("must not be called")])
    router = _router(p1, p2)

    result = await router.generate(_REQ)

    assert result.provider == "p1"
    assert p1.call_count == 1
    assert p2.call_count == 0


# ---------------------------------------------------------------------------
# 2. RateLimited → skip immediately (no retry)
# ---------------------------------------------------------------------------


async def test_rate_limited_skips_to_next() -> None:
    p1 = _FakeProvider("p1", [VideoProviderRateLimited("429")])
    p2 = _FakeProvider("p2", [_result("p2")])
    router = _router(p1, p2)

    result = await router.generate(_REQ)

    assert result.provider == "p2"
    assert p1.call_count == 1   # one attempt, no retry
    assert p2.call_count == 1


# ---------------------------------------------------------------------------
# 3. InvalidOutput → skip immediately (no retry)
# ---------------------------------------------------------------------------


async def test_invalid_output_skips_no_retry() -> None:
    p1 = _FakeProvider("p1", [VideoProviderInvalidOutput("corrupt")])
    p2 = _FakeProvider("p2", [_result("p2")])
    router = _router(p1, p2)

    result = await router.generate(_REQ)

    assert result.provider == "p2"
    assert p1.call_count == 1   # no retries on invalid output
    assert p2.call_count == 1


# ---------------------------------------------------------------------------
# 4. Unavailable → retry with backoff, then succeeds
# ---------------------------------------------------------------------------


async def test_unavailable_retries_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_mock = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", sleep_mock)

    p1 = _FakeProvider(
        "p1",
        [
            VideoProviderUnavailable("503"),
            VideoProviderUnavailable("503 again"),
            _result("p1"),
        ],
    )
    p2 = _FakeProvider("p2", [AssertionError("must not be called")])
    router = VideoProviderRouter(
        providers=[p1, p2],
        max_retries_on_unavailable=3,
        backoff_schedule_seconds=(1.0, 5.0, 15.0),
    )

    result = await router.generate(_REQ)

    assert result.provider == "p1"
    assert p1.call_count == 3
    assert p2.call_count == 0
    assert sleep_mock.await_count == 2
    delays = [call.args[0] for call in sleep_mock.await_args_list]
    assert delays == [1.0, 5.0]


# ---------------------------------------------------------------------------
# 5. Unavailable exhausts retry budget → falls through to next provider
# ---------------------------------------------------------------------------


async def test_unavailable_falls_through_after_budget() -> None:
    p1 = _FakeProvider("p1", [VideoProviderUnavailable("503")] * 10)
    p2 = _FakeProvider("p2", [_result("p2")])
    router = _router(p1, p2, max_retries_on_unavailable=1)

    result = await router.generate(_REQ)

    assert result.provider == "p2"
    assert p1.call_count == 2   # initial + 1 retry
    assert p2.call_count == 1


# ---------------------------------------------------------------------------
# 6. health() → False skips without calling generate
# ---------------------------------------------------------------------------


async def test_health_false_skips_no_generate_call() -> None:
    p1 = _FakeProvider(
        "p1", [AssertionError("must not be called")], healthy=False
    )
    p2 = _FakeProvider("p2", [_result("p2")])
    router = _router(p1, p2)

    result = await router.generate(_REQ)

    assert result.provider == "p2"
    assert p1.call_count == 0
    assert p2.call_count == 1


# ---------------------------------------------------------------------------
# 7. check_health=False skips the health probe
# ---------------------------------------------------------------------------


async def test_check_health_false_does_not_probe() -> None:
    p1 = _FakeProvider("p1", [_result("p1")], healthy=False)
    router = _router(p1, check_health=False)

    result = await router.generate(_REQ)

    assert result.provider == "p1"
    assert p1.call_count == 1


# ---------------------------------------------------------------------------
# 8. NotImplementedError propagates immediately (paid stub)
# ---------------------------------------------------------------------------


async def test_not_implemented_error_propagates() -> None:
    p1 = _FakeProvider("p1", [NotImplementedError("paid stub")])
    p2 = _FakeProvider("p2", [_result("p2")])
    router = _router(p1, p2)

    with pytest.raises(NotImplementedError):
        await router.generate(_REQ)

    assert p1.call_count == 1
    assert p2.call_count == 0   # no failover


# ---------------------------------------------------------------------------
# 9. VideoProviderError (base) propagates immediately (auth / config)
# ---------------------------------------------------------------------------


async def test_base_provider_error_propagates_no_failover() -> None:
    p1 = _FakeProvider("p1", [VideoProviderError("auth failed")])
    p2 = _FakeProvider("p2", [_result("p2")])
    router = _router(p1, p2)

    with pytest.raises(VideoProviderError) as exc:
        await router.generate(_REQ)

    assert "auth failed" in str(exc.value)
    assert not isinstance(exc.value, VideoProviderRateLimited)
    assert not isinstance(exc.value, VideoProviderUnavailable)
    assert not isinstance(exc.value, VideoProviderInvalidOutput)
    assert p1.call_count == 1
    assert p2.call_count == 0


# ---------------------------------------------------------------------------
# Chain exhausted (all providers fail)
# ---------------------------------------------------------------------------


async def test_all_providers_unavailable_raises_provider_error() -> None:
    p1 = _FakeProvider("p1", [VideoProviderUnavailable("503")])
    p2 = _FakeProvider("p2", [VideoProviderUnavailable("503")])
    router = _router(p1, p2, max_retries_on_unavailable=0)

    with pytest.raises(VideoProviderError) as exc:
        await router.generate(_REQ)

    assert "exhausted" in str(exc.value).lower()


async def test_all_providers_rate_limited_raises_provider_error() -> None:
    p1 = _FakeProvider("p1", [VideoProviderRateLimited("429")])
    p2 = _FakeProvider("p2", [VideoProviderRateLimited("429")])
    router = _router(p1, p2)

    with pytest.raises(VideoProviderError):
        await router.generate(_REQ)


async def test_empty_chain_raises_provider_error() -> None:
    router = _router()

    with pytest.raises(VideoProviderError, match="empty"):
        await router.generate(_REQ)


# ---------------------------------------------------------------------------
# Backoff scheduling
# ---------------------------------------------------------------------------


async def test_backoff_clamps_to_last_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When retries exceed backoff length, last value is reused."""
    sleep_mock = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", sleep_mock)

    p1 = _FakeProvider("p1", [VideoProviderUnavailable("503")] * 4)
    router = VideoProviderRouter(
        providers=[p1],
        max_retries_on_unavailable=3,
        backoff_schedule_seconds=(1.0, 2.0),  # only 2 values, 3 retries
    )

    with pytest.raises(VideoProviderError):
        await router.generate(_REQ)

    delays = [call.args[0] for call in sleep_mock.await_args_list]
    assert delays == [1.0, 2.0, 2.0]   # 3rd clamps to last


# ---------------------------------------------------------------------------
# provider_names
# ---------------------------------------------------------------------------


def test_provider_names_returns_names_in_order() -> None:
    p1 = _FakeProvider("alpha", [])
    p2 = _FakeProvider("beta", [])
    router = VideoProviderRouter(providers=[p1, p2])
    assert router.provider_names == ("alpha", "beta")
