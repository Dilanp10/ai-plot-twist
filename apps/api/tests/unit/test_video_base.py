"""Unit tests: VideoRequest + VideoResult dataclasses + exception hierarchy.

Module 012 / Task T-001.

The dataclasses are frozen, so equality, hashability, and immutability are
the load-bearing properties checked here. The exception hierarchy must form
a single tree rooted at :class:`VideoProviderError` so callers can pin one
``except`` clause to catch any T2V provider failure.
"""

from __future__ import annotations

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

# ---------------------------------------------------------------------------
# VideoRequest
# ---------------------------------------------------------------------------


def test_video_request_defaults() -> None:
    req = VideoRequest(prompt="una calle oscura en Buenos Aires", seed=42)
    assert req.prompt == "una calle oscura en Buenos Aires"
    assert req.seed == 42
    assert req.duration_s == 5.0
    assert req.width == 512
    assert req.height == 512
    assert req.fps == 24
    assert req.aspect == "9:16"
    assert req.style_tag is None


def test_video_request_is_frozen() -> None:
    req = VideoRequest(prompt="x", seed=0)
    with pytest.raises(Exception):  # noqa: B017 — FrozenInstanceError not exported
        req.prompt = "y"  # type: ignore[misc]


def test_video_request_equality_and_hash() -> None:
    a = VideoRequest(prompt="x", seed=7, duration_s=5.0, width=512, height=512)
    b = VideoRequest(prompt="x", seed=7, duration_s=5.0, width=512, height=512)
    c = VideoRequest(prompt="x", seed=8, duration_s=5.0, width=512, height=512)
    assert a == b
    assert a != c
    assert hash(a) == hash(b)
    assert hash(a) != hash(c)


def test_video_request_accepts_style_tag() -> None:
    req = VideoRequest(prompt="x", seed=1, style_tag="cinematic")
    assert req.style_tag == "cinematic"


def test_video_request_aspect_variants() -> None:
    for aspect in ("9:16", "16:9", "1:1"):
        req = VideoRequest(prompt="x", seed=0, aspect=aspect)
        assert req.aspect == aspect


# ---------------------------------------------------------------------------
# VideoResult
# ---------------------------------------------------------------------------


def test_video_result_cost_defaults_to_zero() -> None:
    result = VideoResult(
        bytes_=b"\x00\x00\x00\x18ftyp",
        mime_type="video/mp4",
        provider="fake",
        model="fake",
        duration_s=5.0,
        frames_count=121,
        latency_ms=10,
    )
    assert result.cost_usd == 0.0


def test_video_result_is_frozen() -> None:
    result = VideoResult(
        bytes_=b"mp4bytes",
        mime_type="video/mp4",
        provider="hf",
        model="ltx-video",
        duration_s=5.0,
        frames_count=121,
        latency_ms=45000,
    )
    with pytest.raises(Exception):  # noqa: B017
        result.bytes_ = b"changed"  # type: ignore[misc]


def test_video_result_equality() -> None:
    kwargs = dict(
        bytes_=b"clip",
        mime_type="video/mp4",
        provider="fake",
        model="fake",
        duration_s=5.0,
        frames_count=121,
        latency_ms=1,
    )
    a = VideoResult(**kwargs)  # type: ignore[arg-type]
    b = VideoResult(**kwargs)  # type: ignore[arg-type]
    assert a == b


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc_cls",
    [
        VideoProviderRateLimited,
        VideoProviderUnavailable,
        VideoProviderInvalidOutput,
    ],
)
def test_typed_exceptions_subclass_base(exc_cls: type[Exception]) -> None:
    """All typed exceptions must inherit VideoProviderError so a single
    ``except VideoProviderError`` catches every T2V provider failure."""
    assert issubclass(exc_cls, VideoProviderError)


def test_typed_exceptions_are_distinct_siblings() -> None:
    """The three policy-routing subclasses are siblings, not nested."""
    assert not issubclass(VideoProviderRateLimited, VideoProviderUnavailable)
    assert not issubclass(VideoProviderUnavailable, VideoProviderRateLimited)
    assert not issubclass(VideoProviderInvalidOutput, VideoProviderRateLimited)
    assert not issubclass(VideoProviderInvalidOutput, VideoProviderUnavailable)


def test_typed_exceptions_are_exceptions() -> None:
    """Each subclass must be raiseable."""
    for exc_cls in (
        VideoProviderError,
        VideoProviderRateLimited,
        VideoProviderUnavailable,
        VideoProviderInvalidOutput,
    ):
        with pytest.raises(exc_cls):
            raise exc_cls("test")


# ---------------------------------------------------------------------------
# ABC enforcement
# ---------------------------------------------------------------------------


def test_video_provider_is_abstract() -> None:
    """Instantiating VideoProvider directly must fail — all methods are abstract."""
    with pytest.raises(TypeError):
        VideoProvider()  # type: ignore[abstract]


def test_video_provider_concrete_subclass_requires_all_methods() -> None:
    """A partial subclass that omits ``generate`` must also be non-instantiable."""

    class PartialProvider(VideoProvider):
        name = "partial"

        async def health(self) -> bool:
            return True

        @property
        def capabilities(self) -> dict[str, object]:
            return {}

        # generate() intentionally missing

    with pytest.raises(TypeError):
        PartialProvider()  # type: ignore[abstract]
