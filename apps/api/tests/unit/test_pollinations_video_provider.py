"""Unit tests: PollinationsVideoProvider.

Module 012 / Task T-004.

Uses httpx MockTransport — no network required.
URL pattern, x402 mapping, and duration validation are the load-bearing
properties.
"""

from __future__ import annotations

import urllib.parse
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
from app.providers.video.pollinations import PollinationsVideoProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REQ = VideoRequest(prompt="una tormenta eléctrica en la pampa", seed=7)


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _ok_response() -> httpx.Response:
    return httpx.Response(200, content=MINIMAL_MP4, headers={"content-type": "video/mp4"})


# ---------------------------------------------------------------------------
# URL building
# ---------------------------------------------------------------------------


async def test_generate_url_contains_encoded_prompt() -> None:
    captured: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        return _ok_response()

    async with _client(handler) as c:
        p = PollinationsVideoProvider(client=c)
        await p.generate(_REQ)

    url = captured["url"]
    assert "video.pollinations.ai" in url
    assert "/prompt/" in url
    encoded_prompt = urllib.parse.quote(_REQ.prompt, safe="")
    assert encoded_prompt in url


async def test_generate_url_contains_seed_and_model() -> None:
    captured: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        return _ok_response()

    async with _client(handler) as c:
        p = PollinationsVideoProvider(client=c)
        await p.generate(_REQ)

    url = captured["url"]
    assert f"seed={_REQ.seed}" in url
    assert "model=" in url


async def test_generate_uses_get_method() -> None:
    captured: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["method"] = req.method
        return _ok_response()

    async with _client(handler) as c:
        p = PollinationsVideoProvider(client=c)
        await p.generate(_REQ)

    assert captured["method"] == "GET"


async def test_generate_url_includes_dimensions() -> None:
    captured: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        return _ok_response()

    req = VideoRequest(prompt="x", seed=0, width=768, height=512)
    async with _client(handler) as c:
        p = PollinationsVideoProvider(client=c)
        await p.generate(req)

    url = captured["url"]
    assert "width=768" in url
    assert "height=512" in url


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_generate_happy_returns_result() -> None:
    async with _client(lambda _: _ok_response()) as c:
        p = PollinationsVideoProvider(client=c)
        result = await p.generate(_REQ)

    assert result.bytes_ == MINIMAL_MP4
    assert result.mime_type == "video/mp4"
    assert result.provider == "pollinations"
    assert result.duration_s == pytest.approx(5.0, abs=0.01)
    assert result.latency_ms >= 0
    assert result.cost_usd == 0.0


async def test_generate_absent_content_type_still_succeeds() -> None:
    async with _client(lambda _: httpx.Response(200, content=MINIMAL_MP4)) as c:
        p = PollinationsVideoProvider(client=c)
        result = await p.generate(_REQ)
    assert result.bytes_ == MINIMAL_MP4


# ---------------------------------------------------------------------------
# Exception mapping
# ---------------------------------------------------------------------------


async def test_429_raises_rate_limited() -> None:
    async with _client(lambda _: httpx.Response(429, text="too many")) as c:
        p = PollinationsVideoProvider(client=c)
        with pytest.raises(VideoProviderRateLimited):
            await p.generate(_REQ)


async def test_402_x402_raises_rate_limited() -> None:
    body = '{"x402Version":1,"error":"Queue full for IP (max: 1)"}'
    async with _client(lambda _: httpx.Response(402, text=body)) as c:
        p = PollinationsVideoProvider(client=c)
        with pytest.raises(VideoProviderRateLimited, match="x402"):
            await p.generate(_REQ)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_5xx_raises_unavailable(status: int) -> None:
    async with _client(lambda _: httpx.Response(status, text="boom")) as c:
        p = PollinationsVideoProvider(client=c)
        with pytest.raises(VideoProviderUnavailable):
            await p.generate(_REQ)


async def test_other_4xx_raises_base_error() -> None:
    async with _client(lambda _: httpx.Response(404, text="not found")) as c:
        p = PollinationsVideoProvider(client=c)
        with pytest.raises(VideoProviderError) as exc:
            await p.generate(_REQ)
        assert not isinstance(exc.value, VideoProviderRateLimited)
        assert not isinstance(exc.value, VideoProviderUnavailable)
        assert not isinstance(exc.value, VideoProviderInvalidOutput)


async def test_timeout_raises_unavailable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout", request=req)

    async with _client(handler) as c:
        p = PollinationsVideoProvider(client=c)
        with pytest.raises(VideoProviderUnavailable):
            await p.generate(_REQ)


async def test_transport_error_raises_unavailable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns fail", request=req)

    async with _client(handler) as c:
        p = PollinationsVideoProvider(client=c)
        with pytest.raises(VideoProviderUnavailable):
            await p.generate(_REQ)


async def test_empty_body_raises_invalid_output() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"", headers={"content-type": "video/mp4"})

    async with _client(handler) as c:
        p = PollinationsVideoProvider(client=c)
        with pytest.raises(VideoProviderInvalidOutput):
            await p.generate(_REQ)


async def test_non_video_content_type_raises_invalid_output() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"err":"x"}',
            headers={"content-type": "application/json"},
        )

    async with _client(handler) as c:
        p = PollinationsVideoProvider(client=c)
        with pytest.raises(VideoProviderInvalidOutput, match="non-video"):
            await p.generate(_REQ)


async def test_corrupt_mp4_raises_invalid_output() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"\x00" * 50, headers={"content-type": "video/mp4"}
        )

    async with _client(handler) as c:
        p = PollinationsVideoProvider(client=c)
        with pytest.raises(VideoProviderInvalidOutput):
            await p.generate(_REQ)


async def test_clip_too_short_raises_invalid_output() -> None:
    """MINIMAL_MP4 is 5.0 s; requesting 7.0 s means 80% = 5.6 s fails."""
    long_req = VideoRequest(prompt="x", seed=0, duration_s=7.0)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=MINIMAL_MP4, headers={"content-type": "video/mp4"}
        )

    async with _client(handler) as c:
        p = PollinationsVideoProvider(client=c)
        with pytest.raises(VideoProviderInvalidOutput, match="too short"):
            await p.generate(long_req)


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


async def test_health_true_on_200() -> None:
    async with _client(lambda _: httpx.Response(200, content=b"ok")) as c:
        p = PollinationsVideoProvider(client=c)
        assert await p.health() is True


async def test_health_true_on_4xx() -> None:
    async with _client(lambda _: httpx.Response(404, text="not found")) as c:
        p = PollinationsVideoProvider(client=c)
        assert await p.health() is True


async def test_health_false_on_5xx() -> None:
    async with _client(lambda _: httpx.Response(503, text="down")) as c:
        p = PollinationsVideoProvider(client=c)
        assert await p.health() is False


async def test_health_false_on_transport_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns fail", request=req)

    async with _client(handler) as c:
        p = PollinationsVideoProvider(client=c)
        assert await p.health() is False


# ---------------------------------------------------------------------------
# capabilities + name
# ---------------------------------------------------------------------------


def test_capabilities_required_keys() -> None:
    caps = PollinationsVideoProvider().capabilities
    assert "max_duration_s" in caps
    assert "supported_resolutions" in caps
    assert "supported_fps" in caps


def test_name_attribute() -> None:
    assert PollinationsVideoProvider.name == "pollinations"
