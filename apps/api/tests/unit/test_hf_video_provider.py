"""Unit tests: HFVideoProvider + _derive_num_frames + mutagen parseability.

Module 012 / Task T-003.

All HTTP calls are mocked with httpx.MockTransport — no network required.
mutagen parseability of MINIMAL_MP4 is verified here (T-002 deferred it).
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from app.providers.video.base import (
    VideoProviderError,
    VideoProviderInvalidOutput,
    VideoProviderRateLimited,
    VideoProviderUnavailable,
    VideoRequest,
)
from app.providers.video.fake import MINIMAL_MP4
from app.providers.video.hf import (
    HFVideoProvider,
    _derive_num_frames,
    _find_mvhd_duration,
    _parse_mp4_duration,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REQ = VideoRequest(prompt="una calle oscura, lluvia", seed=42)


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _ok_response() -> httpx.Response:
    return httpx.Response(200, content=MINIMAL_MP4, headers={"content-type": "video/mp4"})


# ---------------------------------------------------------------------------
# _derive_num_frames
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("duration_s", "fps", "expected"),
    [
        (5.0, 24, 121),   # raw=120 → n=round(119/8)=round(14.875)=15 → 121
        (1.0, 24, 25),    # raw=24  → n=round(23/8)=round(2.875)=3   → 25
        (0.1, 24, 9),     # raw=2.4 → n=max(1,round(1.4/8))=max(1,0)=1 → 9
        (10.0, 24, 241),  # raw=240 → n=round(239/8)=round(29.875)=30 → 241
        (3.0, 24, 73),    # raw=72  → n=round(71/8)=round(8.875)=9   → 73
    ],
)
def test_derive_num_frames(duration_s: float, fps: int, expected: int) -> None:
    result = _derive_num_frames(duration_s, fps)
    assert result == expected
    assert result % 8 == 1


def test_derive_num_frames_always_satisfies_constraint() -> None:
    for secs in (0.5, 1.0, 2.5, 5.0, 7.5, 10.0):
        n = _derive_num_frames(secs, 24)
        assert n % 8 == 1, f"num_frames={n} is not n*8+1 for duration={secs}"
        assert n >= 1


# ---------------------------------------------------------------------------
# _parse_mp4_duration / _find_mvhd_duration (mvhd struct parsing, not mutagen)
# Note: LTX-Video produces video-only clips; mutagen reads from audio mdhd
# which is absent → we parse mvhd directly.
# ---------------------------------------------------------------------------


def test_parse_mp4_duration_reads_minimal_mp4() -> None:
    """MINIMAL_MP4 has mvhd timescale=1000 duration=5000 → 5.0 s."""
    duration = _parse_mp4_duration(MINIMAL_MP4)
    assert duration == pytest.approx(5.0, abs=0.01)


def test_find_mvhd_duration_version0() -> None:
    """Hand-craft an mvhd v0 and verify the formula."""
    duration = _find_mvhd_duration(MINIMAL_MP4, 0, len(MINIMAL_MP4))
    assert duration == pytest.approx(5.0, abs=0.01)


def test_parse_mp4_duration_invalid_bytes_raises() -> None:
    with pytest.raises(VideoProviderInvalidOutput, match="corrupt"):
        _parse_mp4_duration(b"this is not an mp4")


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_empty_token_raises() -> None:
    with pytest.raises(ValueError, match="non-empty token"):
        HFVideoProvider(token="")


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


async def test_generate_posts_to_correct_endpoint() -> None:
    captured: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["method"] = req.method
        captured["auth"] = req.headers.get("authorization")
        captured["body"] = json.loads(req.content)
        return _ok_response()

    async with _client(handler) as c:
        p = HFVideoProvider(token="hf_secret", client=c)
        await p.generate(_REQ)

    assert captured["method"] == "POST"
    assert "Lightricks/LTX-Video" in str(captured["url"])
    assert captured["auth"] == "Bearer hf_secret"

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["inputs"] == "una calle oscura, lluvia"
    params = body["parameters"]
    assert params["seed"] == 42
    assert params["num_inference_steps"] == 50
    assert params["guidance_scale"] == pytest.approx(3.0)
    assert params["num_frames"] % 8 == 1


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_generate_happy_returns_result() -> None:
    async with _client(lambda _: _ok_response()) as c:
        p = HFVideoProvider(token="x", client=c)
        result = await p.generate(_REQ)

    assert result.bytes_ == MINIMAL_MP4
    assert result.mime_type == "video/mp4"
    assert result.provider == "hf"
    assert result.model == "ltx-video"
    assert result.duration_s == pytest.approx(5.0, abs=0.01)
    assert result.latency_ms >= 0
    assert result.cost_usd == 0.0
    assert result.frames_count % 8 == 1


async def test_generate_absent_content_type_still_succeeds() -> None:
    """HF sometimes omits Content-Type; mutagen is the authoritative check."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=MINIMAL_MP4)

    async with _client(handler) as c:
        p = HFVideoProvider(token="x", client=c)
        result = await p.generate(_REQ)

    assert result.bytes_ == MINIMAL_MP4


# ---------------------------------------------------------------------------
# Cold start
# ---------------------------------------------------------------------------


async def test_503_with_estimated_time_raises_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={"error": "Model is loading", "estimated_time": 20.0},
        )

    async with _client(handler) as c:
        p = HFVideoProvider(token="x", client=c)
        with pytest.raises(VideoProviderUnavailable) as exc:
            await p.generate(_REQ)
        assert "cold start" in str(exc.value).lower()


async def test_503_without_estimated_time_raises_unavailable_not_cold_start() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    async with _client(handler) as c:
        p = HFVideoProvider(token="x", client=c)
        with pytest.raises(VideoProviderUnavailable) as exc:
            await p.generate(_REQ)
        assert "cold start" not in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Exception mapping
# ---------------------------------------------------------------------------


async def test_429_raises_rate_limited() -> None:
    async with _client(lambda _: httpx.Response(429, text="too many")) as c:
        p = HFVideoProvider(token="x", client=c)
        with pytest.raises(VideoProviderRateLimited):
            await p.generate(_REQ)


@pytest.mark.parametrize("status", [500, 502, 504])
async def test_5xx_raises_unavailable(status: int) -> None:
    async with _client(lambda _: httpx.Response(status, text="boom")) as c:
        p = HFVideoProvider(token="x", client=c)
        with pytest.raises(VideoProviderUnavailable):
            await p.generate(_REQ)


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_failure_raises_base_error(status: int) -> None:
    async with _client(lambda _: httpx.Response(status, text="forbidden")) as c:
        p = HFVideoProvider(token="x", client=c)
        with pytest.raises(VideoProviderError) as exc:
            await p.generate(_REQ)
        assert not isinstance(exc.value, VideoProviderRateLimited)
        assert not isinstance(exc.value, VideoProviderUnavailable)
        assert not isinstance(exc.value, VideoProviderInvalidOutput)


async def test_timeout_raises_unavailable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout", request=req)

    async with _client(handler) as c:
        p = HFVideoProvider(token="x", client=c)
        with pytest.raises(VideoProviderUnavailable):
            await p.generate(_REQ)


async def test_transport_error_raises_unavailable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns fail", request=req)

    async with _client(handler) as c:
        p = HFVideoProvider(token="x", client=c)
        with pytest.raises(VideoProviderUnavailable):
            await p.generate(_REQ)


async def test_empty_body_raises_invalid_output() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"", headers={"content-type": "video/mp4"})

    async with _client(handler) as c:
        p = HFVideoProvider(token="x", client=c)
        with pytest.raises(VideoProviderInvalidOutput):
            await p.generate(_REQ)


async def test_non_video_content_type_raises_invalid_output() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"error":"x"}',
            headers={"content-type": "application/json"},
        )

    async with _client(handler) as c:
        p = HFVideoProvider(token="x", client=c)
        with pytest.raises(VideoProviderInvalidOutput, match="non-video"):
            await p.generate(_REQ)


async def test_corrupt_mp4_raises_invalid_output() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\x00" * 50, headers={"content-type": "video/mp4"})

    async with _client(handler) as c:
        p = HFVideoProvider(token="x", client=c)
        with pytest.raises(VideoProviderInvalidOutput, match="corrupt"):
            await p.generate(_REQ)


async def test_clip_too_short_raises_invalid_output() -> None:
    """MINIMAL_MP4 is 5.0 s; requesting 7.0 s means 80% threshold = 5.6 s → fail."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=MINIMAL_MP4, headers={"content-type": "video/mp4"})

    long_req = VideoRequest(prompt="x", seed=0, duration_s=7.0)
    async with _client(handler) as c:
        p = HFVideoProvider(token="x", client=c)
        with pytest.raises(VideoProviderInvalidOutput, match="too short"):
            await p.generate(long_req)


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


async def test_health_true_on_200() -> None:
    async with _client(lambda _: httpx.Response(200, content=b"ok")) as c:
        p = HFVideoProvider(token="x", client=c)
        assert await p.health() is True


async def test_health_true_on_401() -> None:
    """401 means auth needed (API is up); health should pass."""
    async with _client(lambda _: httpx.Response(401, text="unauthorized")) as c:
        p = HFVideoProvider(token="x", client=c)
        assert await p.health() is True


async def test_health_false_on_5xx() -> None:
    async with _client(lambda _: httpx.Response(503, text="down")) as c:
        p = HFVideoProvider(token="x", client=c)
        assert await p.health() is False


async def test_health_false_on_transport_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns fail", request=req)

    async with _client(handler) as c:
        p = HFVideoProvider(token="x", client=c)
        assert await p.health() is False


# ---------------------------------------------------------------------------
# capabilities
# ---------------------------------------------------------------------------


def test_capabilities_required_keys() -> None:
    caps = HFVideoProvider(token="x").capabilities
    assert "max_duration_s" in caps
    assert "supported_resolutions" in caps
    assert "supported_fps" in caps
