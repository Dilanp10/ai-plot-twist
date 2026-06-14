"""Unit tests: ImageRequest + ImageResult dataclasses + exception hierarchy.

Module 009 / Task T-001.

The dataclasses are frozen, so equality + hashability + immutability are
the load-bearing properties. The exception hierarchy must form a single
tree rooted at :class:`ImageProviderError` so callers can pin one
``except`` clause to catch any provider failure.
"""

from __future__ import annotations

import pytest

from app.providers.image import (
    ImageProvider,
    ImageProviderError,
    ImageProviderInvalidOutput,
    ImageProviderRateLimited,
    ImageProviderUnavailable,
    ImageRequest,
    ImageResult,
)

# ---------------------------------------------------------------------------
# ImageRequest
# ---------------------------------------------------------------------------


def test_image_request_defaults() -> None:
    req = ImageRequest(prompt="un perro", seed=42)
    assert req.prompt == "un perro"
    assert req.seed == 42
    assert req.width == 1024
    assert req.height == 1024
    assert req.aspect == "1:1"
    assert req.style_tag is None


def test_image_request_is_frozen() -> None:
    req = ImageRequest(prompt="x", seed=0)
    with pytest.raises(Exception):  # noqa: B017 — FrozenInstanceError, not exported
        req.prompt = "y"  # type: ignore[misc]


def test_image_request_equality() -> None:
    a = ImageRequest(prompt="x", seed=7, width=512, height=512)
    b = ImageRequest(prompt="x", seed=7, width=512, height=512)
    c = ImageRequest(prompt="x", seed=8, width=512, height=512)
    assert a == b
    assert a != c
    assert hash(a) == hash(b)


def test_image_request_accepts_style_tag() -> None:
    req = ImageRequest(prompt="x", seed=1, style_tag="sdxl-cinematic")
    assert req.style_tag == "sdxl-cinematic"


# ---------------------------------------------------------------------------
# ImageResult
# ---------------------------------------------------------------------------


def test_image_result_defaults_cost_zero() -> None:
    res = ImageResult(
        bytes_=b"\x89PNG",
        mime_type="image/png",
        provider="fake",
        model="fake:1x1-png",
        latency_ms=3,
    )
    assert res.cost_usd == 0.0


def test_image_result_is_frozen() -> None:
    res = ImageResult(
        bytes_=b"",
        mime_type="image/webp",
        provider="pollinations",
        model="flux",
        latency_ms=100,
    )
    with pytest.raises(Exception):  # noqa: B017
        res.bytes_ = b"changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc_cls",
    [
        ImageProviderRateLimited,
        ImageProviderUnavailable,
        ImageProviderInvalidOutput,
    ],
)
def test_typed_exceptions_subclass_base(
    exc_cls: type[Exception],
) -> None:
    """All typed exceptions must inherit ImageProviderError so a single
    ``except ImageProviderError`` catches every provider failure."""
    assert issubclass(exc_cls, ImageProviderError)


def test_typed_exceptions_are_distinct_branches() -> None:
    """The three policy-routing subclasses are siblings, not nested."""
    assert not issubclass(ImageProviderRateLimited, ImageProviderUnavailable)
    assert not issubclass(ImageProviderUnavailable, ImageProviderRateLimited)
    assert not issubclass(ImageProviderInvalidOutput, ImageProviderRateLimited)
    assert not issubclass(ImageProviderInvalidOutput, ImageProviderUnavailable)


# ---------------------------------------------------------------------------
# ABC enforcement
# ---------------------------------------------------------------------------


def test_image_provider_is_abstract() -> None:
    """Instantiating ImageProvider directly must fail — every method is abstract."""
    with pytest.raises(TypeError):
        ImageProvider()  # type: ignore[abstract]
