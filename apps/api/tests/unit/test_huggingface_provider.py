"""Unit tests: HuggingFaceProvider.

Module 009 / Task T-004.

Uses httpx ``MockTransport`` so the tests run without network. The body
shape, headers, and cold-start handling are the load-bearing properties.
"""

from __future__ import annotations

import json
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
from app.providers.image.huggingface import HuggingFaceProvider


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


def test_empty_token_raises() -> None:
    with pytest.raises(ValueError, match="non-empty token"):
        HuggingFaceProvider(token="")


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


async def test_generate_posts_expected_body_and_headers() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, content=b"\x89PNG\x00", headers={"content-type": "image/png"}
        )

    async with _client(handler) as c:
        p = HuggingFaceProvider(token="hf_secret", client=c)
        await p.generate(
            ImageRequest(prompt="un gato", seed=7, width=512, height=768)
        )

    assert captured["method"] == "POST"
    assert str(captured["url"]).endswith(
        "/models/black-forest-labs/FLUX.1-schnell"
    )
    assert captured["auth"] == "Bearer hf_secret"
    body = captured["body"]
    assert body == {
        "inputs": "un gato",
        "parameters": {"seed": 7, "width": 512, "height": 768},
    }


async def test_generate_honors_custom_model() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200, content=b"\xff\xd8", headers={"content-type": "image/jpeg"}
        )

    async with _client(handler) as c:
        p = HuggingFaceProvider(
            token="x", client=c, model="stabilityai/stable-diffusion-xl-base-1.0"
        )
        await p.generate(ImageRequest(prompt="x", seed=0))

    assert captured["url"].endswith(
        "/models/stabilityai/stable-diffusion-xl-base-1.0"
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_generate_happy_returns_result() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"WEBPbytes", headers={"content-type": "image/webp"}
        )

    async with _client(handler) as c:
        p = HuggingFaceProvider(token="x", client=c)
        result = await p.generate(ImageRequest(prompt="x", seed=0))

    assert result.bytes_ == b"WEBPbytes"
    assert result.mime_type == "image/webp"
    assert result.provider == "hf"
    assert result.model == "black-forest-labs/FLUX.1-schnell"
    assert result.latency_ms >= 0


# ---------------------------------------------------------------------------
# Cold start
# ---------------------------------------------------------------------------


async def test_503_with_estimated_time_raises_unavailable() -> None:
    """The canonical HF cold-start signal must map to Unavailable so the
    router retries with backoff."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "error": "Model is currently loading",
                "estimated_time": 18.4,
            },
        )

    async with _client(handler) as c:
        p = HuggingFaceProvider(token="x", client=c)
        with pytest.raises(ImageProviderUnavailable) as exc:
            await p.generate(ImageRequest(prompt="x", seed=0))
        assert "cold start" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Exception mapping
# ---------------------------------------------------------------------------


async def test_429_raises_rate_limited() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limited")

    async with _client(handler) as c:
        p = HuggingFaceProvider(token="x", client=c)
        with pytest.raises(ImageProviderRateLimited):
            await p.generate(ImageRequest(prompt="x", seed=0))


@pytest.mark.parametrize("status", [500, 502, 504])
async def test_non_503_5xx_raises_unavailable(status: int) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="boom")

    async with _client(handler) as c:
        p = HuggingFaceProvider(token="x", client=c)
        with pytest.raises(ImageProviderUnavailable):
            await p.generate(ImageRequest(prompt="x", seed=0))


async def test_503_without_estimated_time_still_unavailable() -> None:
    """A plain 503 (not a cold start) is still Unavailable — but the
    message should NOT claim it was a cold start."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service is down")

    async with _client(handler) as c:
        p = HuggingFaceProvider(token="x", client=c)
        with pytest.raises(ImageProviderUnavailable) as exc:
            await p.generate(ImageRequest(prompt="x", seed=0))
        assert "cold start" not in str(exc.value).lower()


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_failure_raises_base_error(status: int) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="unauthorized")

    async with _client(handler) as c:
        p = HuggingFaceProvider(token="x", client=c)
        with pytest.raises(ImageProviderError) as exc:
            await p.generate(ImageRequest(prompt="x", seed=0))
        # Must NOT be one of the policy-routing subclasses — operators
        # need to see this immediately, not have the router retry it.
        assert not isinstance(exc.value, ImageProviderRateLimited)
        assert not isinstance(exc.value, ImageProviderUnavailable)
        assert not isinstance(exc.value, ImageProviderInvalidOutput)


async def test_timeout_raises_unavailable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timeout", request=req)

    async with _client(handler) as c:
        p = HuggingFaceProvider(token="x", client=c)
        with pytest.raises(ImageProviderUnavailable):
            await p.generate(ImageRequest(prompt="x", seed=0))


async def test_non_image_content_type_raises_invalid_output() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b'{"error":"x"}', headers={"content-type": "application/json"}
        )

    async with _client(handler) as c:
        p = HuggingFaceProvider(token="x", client=c)
        with pytest.raises(ImageProviderInvalidOutput):
            await p.generate(ImageRequest(prompt="x", seed=0))


async def test_empty_body_raises_invalid_output() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"", headers={"content-type": "image/png"}
        )

    async with _client(handler) as c:
        p = HuggingFaceProvider(token="x", client=c)
        with pytest.raises(ImageProviderInvalidOutput):
            await p.generate(ImageRequest(prompt="x", seed=0))


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


async def test_health_true_on_200() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok")

    async with _client(handler) as c:
        p = HuggingFaceProvider(token="x", client=c)
        assert await p.health() is True


async def test_health_false_on_5xx() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    async with _client(handler) as c:
        p = HuggingFaceProvider(token="x", client=c)
        assert await p.health() is False


async def test_health_false_on_transport_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns fail", request=req)

    async with _client(handler) as c:
        p = HuggingFaceProvider(token="x", client=c)
        assert await p.health() is False
