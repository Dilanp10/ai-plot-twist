"""Unit tests: FakeVideoProvider + MINIMAL_MP4.

Module 012 / Task T-002.

Note: mutagen parseability of MINIMAL_MP4 is asserted in T-003's test file
(``test_hf_video_provider.py``) once mutagen is added as a dependency.
"""

from __future__ import annotations

import asyncio

import pytest

from app.providers.video.base import (
    VideoProviderRateLimited,
    VideoProviderUnavailable,
    VideoRequest,
    VideoResult,
)
from app.providers.video.fake import MINIMAL_MP4, FakeVideoProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REQ = VideoRequest(prompt="cualquier prompt", seed=0)


def _result(provider: str = "fake", duration_s: float = 5.0) -> VideoResult:
    return VideoResult(
        bytes_=MINIMAL_MP4,
        mime_type="video/mp4",
        provider=provider,
        model="fake",
        duration_s=duration_s,
        frames_count=121,
        latency_ms=0,
    )


# ---------------------------------------------------------------------------
# MINIMAL_MP4 structure
# ---------------------------------------------------------------------------


def test_minimal_mp4_non_empty() -> None:
    assert len(MINIMAL_MP4) == 136


def test_minimal_mp4_has_ftyp_magic() -> None:
    assert b"ftyp" in MINIMAL_MP4


def test_minimal_mp4_has_moov() -> None:
    assert b"moov" in MINIMAL_MP4


def test_minimal_mp4_has_mvhd() -> None:
    assert b"mvhd" in MINIMAL_MP4


def test_minimal_mp4_brand_mp42() -> None:
    assert b"mp42" in MINIMAL_MP4


# ---------------------------------------------------------------------------
# Infinite mode (responses=None)
# ---------------------------------------------------------------------------


async def test_infinite_mode_returns_result() -> None:
    p = FakeVideoProvider()
    result = await p.generate(_REQ)
    assert result.bytes_ == MINIMAL_MP4
    assert result.mime_type == "video/mp4"
    assert result.provider == "fake"
    assert result.duration_s == 5.0
    assert result.cost_usd == 0.0


async def test_infinite_mode_never_exhausts() -> None:
    p = FakeVideoProvider()
    for _ in range(10):
        result = await p.generate(_REQ)
        assert result.bytes_ == MINIMAL_MP4


# ---------------------------------------------------------------------------
# Injectable mode (responses=[...])
# ---------------------------------------------------------------------------


async def test_injectable_pops_in_order() -> None:
    r1 = _result(provider="hf")
    r2 = _result(provider="pollinations")
    p = FakeVideoProvider(responses=[r1, r2])
    assert await p.generate(_REQ) == r1
    assert await p.generate(_REQ) == r2


async def test_injectable_exhaustion_raises_unavailable() -> None:
    p = FakeVideoProvider(responses=[_result()])
    await p.generate(_REQ)  # consume the only item
    with pytest.raises(VideoProviderUnavailable, match="exhausted"):
        await p.generate(_REQ)


async def test_injectable_exception_instance_is_raised() -> None:
    exc = VideoProviderRateLimited("quota hit")
    p = FakeVideoProvider(responses=[exc])
    with pytest.raises(VideoProviderRateLimited, match="quota hit"):
        await p.generate(_REQ)


async def test_injectable_exception_class_is_instantiated_and_raised() -> None:
    p = FakeVideoProvider(responses=[VideoProviderUnavailable])
    with pytest.raises(VideoProviderUnavailable):
        await p.generate(_REQ)


async def test_injectable_mixed_results_and_exceptions() -> None:
    p = FakeVideoProvider(
        responses=[
            _result(),
            VideoProviderRateLimited("429"),
            _result(),
        ]
    )
    r1 = await p.generate(_REQ)
    assert r1.provider == "fake"

    with pytest.raises(VideoProviderRateLimited):
        await p.generate(_REQ)

    r3 = await p.generate(_REQ)
    assert r3.provider == "fake"


# ---------------------------------------------------------------------------
# latency_ms
# ---------------------------------------------------------------------------


async def test_latency_ms_zero_is_fast() -> None:
    import time

    p = FakeVideoProvider(latency_ms=0)
    start = time.monotonic()
    await p.generate(_REQ)
    assert (time.monotonic() - start) < 0.05  # < 50 ms


async def test_latency_ms_nonzero_sleeps() -> None:
    import time

    p = FakeVideoProvider(latency_ms=50)
    start = time.monotonic()
    await p.generate(_REQ)
    assert (time.monotonic() - start) >= 0.045


# ---------------------------------------------------------------------------
# health_returns
# ---------------------------------------------------------------------------


async def test_health_returns_true_by_default() -> None:
    p = FakeVideoProvider()
    assert await p.health() is True


async def test_health_returns_false_when_configured() -> None:
    p = FakeVideoProvider(health_returns=False)
    assert await p.health() is False


# ---------------------------------------------------------------------------
# capabilities
# ---------------------------------------------------------------------------


def test_capabilities_required_keys() -> None:
    caps = FakeVideoProvider().capabilities
    assert "max_duration_s" in caps
    assert "supported_resolutions" in caps
    assert "supported_fps" in caps


def test_name_attribute() -> None:
    assert FakeVideoProvider.name == "fake"


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


async def test_concurrent_infinite_calls_are_independent() -> None:
    p = FakeVideoProvider()
    results = await asyncio.gather(
        p.generate(_REQ),
        p.generate(_REQ),
        p.generate(_REQ),
    )
    assert all(r.bytes_ == MINIMAL_MP4 for r in results)


async def test_concurrent_injectable_calls_pop_correctly() -> None:
    items = [_result() for _ in range(4)]
    p = FakeVideoProvider(responses=list(items))
    results = await asyncio.gather(*(p.generate(_REQ) for _ in range(4)))
    assert len(results) == 4
