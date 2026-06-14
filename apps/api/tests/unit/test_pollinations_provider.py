"""Unit tests: PollinationsProvider.

Module 009 / Task T-003.

Uses an httpx ``MockTransport`` so no network I/O happens. The URL
pattern + exception mapping are the load-bearing properties.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Callable

import httpx
import pytest

from app.providers.image import (
    ImageProviderError,
    ImageProviderInvalidOutput,
    ImageProviderRateLimited,
    ImageProviderUnavailable,
    ImageRequest,
)
from app.providers.image.pollinations import PollinationsProvider


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# URL building
# ---------------------------------------------------------------------------


async def test_generate_sends_url_with_expected_params() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            content=b"\x89PNG\x00",
            headers={"content-type": "image/png"},
        )

    async with _client(handler) as c:
        p = PollinationsProvider(client=c)
        await p.generate(
            ImageRequest(prompt="un perro feliz", seed=42, width=1024, height=1024)
        )

    url = captured["url"]
    assert url.startswith("https://image.pollinations.ai/prompt/")
    assert urllib.parse.quote("un perro feliz", safe="") in url
    assert "width=1024" in url
    assert "height=1024" in url
    assert "seed=42" in url
    assert "model=flux" in url
    assert "nologo=true" in url
    assert "enhance=false" in url


async def test_generate_url_encodes_special_chars() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200, content=b"\xff\xd8", headers={"content-type": "image/jpeg"}
        )

    async with _client(handler) as c:
        p = PollinationsProvider(client=c)
        await p.generate(ImageRequest(prompt="hola, mundo & cía", seed=1))

    # The encoded path segment must escape `,`, ` `, and `&`.
    assert "hola%2C%20mundo%20%26%20c%C3%ADa" in captured["url"]


async def test_generate_passes_through_custom_model() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200, content=b"\x89PNG", headers={"content-type": "image/png"}
        )

    async with _client(handler) as c:
        p = PollinationsProvider(client=c, model="sdxl")
        await p.generate(ImageRequest(prompt="x", seed=0))

    assert "model=sdxl" in captured["url"]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_generate_happy_returns_result() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"WEBPdata", headers={"content-type": "image/webp"}
        )

    async with _client(handler) as c:
        p = PollinationsProvider(client=c)
        result = await p.generate(ImageRequest(prompt="x", seed=1))

    assert result.bytes_ == b"WEBPdata"
    assert result.mime_type == "image/webp"
    assert result.provider == "pollinations"
    assert result.model == "flux"
    assert result.latency_ms >= 0
    assert result.cost_usd == 0.0


# ---------------------------------------------------------------------------
# Exception mapping
# ---------------------------------------------------------------------------


async def test_429_raises_rate_limited() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    async with _client(handler) as c:
        p = PollinationsProvider(client=c)
        with pytest.raises(ImageProviderRateLimited):
            await p.generate(ImageRequest(prompt="x", seed=0))


async def test_402_x402_queue_full_raises_rate_limited() -> None:
    """The x402 'Queue full for IP' free-tier signal must map to RateLimited.

    Real response observed against image.pollinations.ai mid-2026; the
    router must skip to the next provider instead of raising the base
    error (which would otherwise leak to the operator as a config bug).
    """

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            402,
            json={
                "x402Version": 1,
                "error": (
                    "Queue full for IP: 2a06:98c0:3600::103: 1 requests "
                    "already queued (max: 1). Get unlimited access..."
                ),
            },
        )

    async with _client(handler) as c:
        p = PollinationsProvider(client=c)
        with pytest.raises(ImageProviderRateLimited) as exc:
            await p.generate(ImageRequest(prompt="x", seed=0))
        assert "402" in str(exc.value)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_5xx_raises_unavailable(status: int) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="boom")

    async with _client(handler) as c:
        p = PollinationsProvider(client=c)
        with pytest.raises(ImageProviderUnavailable):
            await p.generate(ImageRequest(prompt="x", seed=0))


async def test_timeout_raises_unavailable() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout", request=_)

    async with _client(handler) as c:
        p = PollinationsProvider(client=c)
        with pytest.raises(ImageProviderUnavailable):
            await p.generate(ImageRequest(prompt="x", seed=0))


async def test_4xx_other_than_429_raises_base_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    async with _client(handler) as c:
        p = PollinationsProvider(client=c)
        with pytest.raises(ImageProviderError) as exc:
            await p.generate(ImageRequest(prompt="x", seed=0))
        # Must NOT be one of the policy-routing subclasses.
        assert not isinstance(exc.value, ImageProviderRateLimited)
        assert not isinstance(exc.value, ImageProviderUnavailable)
        assert not isinstance(exc.value, ImageProviderInvalidOutput)


async def test_non_image_content_type_raises_invalid_output() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"hello", headers={"content-type": "text/html"}
        )

    async with _client(handler) as c:
        p = PollinationsProvider(client=c)
        with pytest.raises(ImageProviderInvalidOutput):
            await p.generate(ImageRequest(prompt="x", seed=0))


async def test_empty_body_raises_invalid_output() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"", headers={"content-type": "image/png"}
        )

    async with _client(handler) as c:
        p = PollinationsProvider(client=c)
        with pytest.raises(ImageProviderInvalidOutput):
            await p.generate(ImageRequest(prompt="x", seed=0))


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


async def test_health_true_on_200() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok")

    async with _client(handler) as c:
        p = PollinationsProvider(client=c)
        assert await p.health() is True


async def test_health_false_on_5xx() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    async with _client(handler) as c:
        p = PollinationsProvider(client=c)
        assert await p.health() is False


async def test_health_false_on_transport_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns fail", request=req)

    async with _client(handler) as c:
        p = PollinationsProvider(client=c)
        assert await p.health() is False
