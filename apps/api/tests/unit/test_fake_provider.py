"""Unit tests: FakeImageProvider.

Module 009 / Task T-002.
"""

from __future__ import annotations

import pytest

from app.providers.image import ImageRequest
from app.providers.image.fake import FakeImageProvider, PNG_1x1


def test_png_1x1_signature() -> None:
    """The canonical PNG starts with the 8-byte PNG magic + ends with IEND."""
    assert PNG_1x1.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"IHDR" in PNG_1x1
    assert b"IDAT" in PNG_1x1
    assert b"IEND" in PNG_1x1
    assert PNG_1x1.endswith(b"\xaeB`\x82")  # IEND CRC


async def test_health_always_true() -> None:
    p = FakeImageProvider()
    assert await p.health() is True


async def test_generate_returns_png_1x1() -> None:
    p = FakeImageProvider()
    result = await p.generate(ImageRequest(prompt="cualquier cosa", seed=0))
    assert result.bytes_ == PNG_1x1
    assert result.mime_type == "image/png"
    assert result.provider == "fake"
    assert result.model == "fake:1x1-png"
    assert result.latency_ms == 0
    assert result.cost_usd == 0.0


async def test_generate_ignores_prompt() -> None:
    """The same bytes come back regardless of prompt/seed/aspect."""
    p = FakeImageProvider()
    r1 = await p.generate(ImageRequest(prompt="A", seed=1))
    r2 = await p.generate(ImageRequest(prompt="B", seed=999, aspect="16:9"))
    assert r1.bytes_ == r2.bytes_


def test_capabilities_shape() -> None:
    caps = FakeImageProvider().capabilities
    assert caps["max_resolution"] == (1, 1)
    assert caps["supports_seed"] is False
    assert "fake:1x1-png" in caps["supported_models"]


def test_name_attribute() -> None:
    assert FakeImageProvider.name == "fake"


@pytest.mark.asyncio
async def test_concurrent_calls_independent() -> None:
    """Two concurrent calls should not interfere with each other."""
    import asyncio

    p = FakeImageProvider()
    results = await asyncio.gather(
        p.generate(ImageRequest(prompt="x", seed=0)),
        p.generate(ImageRequest(prompt="y", seed=1)),
    )
    assert all(r.bytes_ == PNG_1x1 for r in results)
