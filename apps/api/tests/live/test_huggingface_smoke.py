"""Live smoke: HuggingFaceProvider → real api-inference.huggingface.co.

Module 009 / Task T-009.

Marked ``@pytest.mark.live``. Skipped automatically when
``HUGGINGFACE_TOKEN`` is absent (which is the case on PR CI; only the
nightly live-llm-smoke workflow has the secret).

Cold-start tolerance:
  FLUX.1-schnell often returns 503 ``estimated_time`` on the first
  warm-up of the day. The PollinationsProvider tests cover the happy
  path; this test asserts that the cold-start signal is mapped to
  :class:`ImageProviderUnavailable` (= retryable) rather than crashing.
"""

from __future__ import annotations

import os

import pytest

from app.providers.image import (
    ImageProviderUnavailable,
    ImageRequest,
)
from app.providers.image.huggingface import HuggingFaceProvider

pytestmark = pytest.mark.live


def _token_or_skip() -> str:
    token = os.environ.get("HUGGINGFACE_TOKEN")
    if not token:
        pytest.skip("HUGGINGFACE_TOKEN no está seteado; skip live smoke.")
    return token


async def test_huggingface_returns_image_or_cold_start() -> None:
    """One real call → either valid image bytes or a typed cold-start signal.

    Both outcomes prove the wire format + exception mapping is correct.
    """
    token = _token_or_skip()
    p = HuggingFaceProvider(token=token)
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
        except ImageProviderUnavailable as exc:
            # Cold start (503 with estimated_time) is an acceptable outcome.
            assert "cold start" in str(exc).lower() or "503" in str(exc)
            return
    finally:
        await p.aclose()

    assert len(result.bytes_) > 0
    assert result.mime_type in ("image/webp", "image/png", "image/jpeg")
    assert result.provider == "hf"
    assert result.latency_ms > 0


async def test_huggingface_health_true() -> None:
    """The API root must answer the / probe within 2 s."""
    token = _token_or_skip()
    p = HuggingFaceProvider(token=token)
    try:
        assert await p.health() is True
    finally:
        await p.aclose()
