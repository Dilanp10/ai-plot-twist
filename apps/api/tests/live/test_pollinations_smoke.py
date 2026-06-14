"""Live smoke: PollinationsProvider → real image.pollinations.ai.

Module 009 / Task T-009.

Marked ``@pytest.mark.live`` so the regular CI suite (``-m "not live"``)
excludes it. Pollinations is no-auth so the test always has the
credentials it needs; the only gate is the marker, which lets the
nightly workflow opt in explicitly.

Run manually::

    uv run pytest -m live -v tests/live/test_pollinations_smoke.py
"""

from __future__ import annotations

import pytest

from app.providers.image import (
    ImageProviderRateLimited,
    ImageProviderUnavailable,
    ImageRequest,
)
from app.providers.image.pollinations import PollinationsProvider

pytestmark = pytest.mark.live


async def test_pollinations_returns_image_or_typed_rate_limit() -> None:
    """One real GET → either valid image bytes OR a typed RateLimited/Unavailable.

    Pollinations rolled out the x402 free-tier queue cap in mid-2026, so
    the queue-full 402 is a routine outcome even with a healthy service.
    The router's contract is what we care about: typed exceptions for
    skip-to-next behavior, not whether a single shared IP happens to be
    inside the queue at test time.
    """
    p = PollinationsProvider()
    try:
        try:
            result = await p.generate(
                ImageRequest(
                    prompt=(
                        "a black cat sitting on a wooden fence at sunset, "
                        "cinematic, 35mm film, moody lighting"
                    ),
                    seed=42,
                    width=512,
                    height=512,
                )
            )
        except ImageProviderRateLimited as exc:
            assert "402" in str(exc) or "429" in str(exc)
            return
        except ImageProviderUnavailable:
            return  # 5xx / timeout — acceptable outcome
    finally:
        await p.aclose()

    assert len(result.bytes_) > 0
    assert result.mime_type in ("image/webp", "image/png", "image/jpeg")
    assert result.provider == "pollinations"
    assert result.model == "flux"
    assert result.latency_ms > 0


async def test_pollinations_health_true() -> None:
    """The service must answer the / probe within 2 s."""
    p = PollinationsProvider()
    try:
        assert await p.health() is True
    finally:
        await p.aclose()
