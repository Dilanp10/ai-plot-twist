"""Unit tests: compute_r2_path.

Module 009 / Task T-006.
"""

from __future__ import annotations

import pytest

from app.providers.image.base import ImageResult
from app.providers.image.paths import (
    UnsupportedMimeType,
    compute_r2_path,
)


def _result(
    mime: str = "image/webp",
    body: bytes = b"hello world",
) -> ImageResult:
    return ImageResult(
        bytes_=body,
        mime_type=mime,  # type: ignore[arg-type]
        provider="fake",
        model="fake:test",
        latency_ms=0,
    )


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_path_shape() -> None:
    path = compute_r2_path(
        season_slug="mi-temporada",
        chapter_public_id="11111111-1111-1111-1111-111111111111",
        panel_idx=2,
        image_result=_result(),
    )
    assert path.startswith(
        "seasons/mi-temporada/11111111-1111-1111-1111-111111111111/2-"
    )
    assert path.endswith(".webp")


def test_extension_mapping_png() -> None:
    path = compute_r2_path("s", "u", 0, _result(mime="image/png"))
    assert path.endswith(".png")


def test_extension_mapping_jpeg() -> None:
    path = compute_r2_path("s", "u", 0, _result(mime="image/jpeg"))
    assert path.endswith(".jpg")


def test_unsupported_mime_raises() -> None:
    bad = ImageResult(
        bytes_=b"x",
        mime_type="image/gif",  # type: ignore[arg-type]
        provider="fake",
        model="fake:test",
        latency_ms=0,
    )
    with pytest.raises(UnsupportedMimeType):
        compute_r2_path("s", "u", 0, bad)


# ---------------------------------------------------------------------------
# Determinism / content-addressing
# ---------------------------------------------------------------------------


def test_same_bytes_same_path() -> None:
    """Re-rendering the SAME panel with the SAME bytes yields the SAME path."""
    a = compute_r2_path("s", "u", 0, _result(body=b"image-A"))
    b = compute_r2_path("s", "u", 0, _result(body=b"image-A"))
    assert a == b


def test_different_bytes_different_path() -> None:
    """Different bytes → different hash → different path."""
    a = compute_r2_path("s", "u", 0, _result(body=b"image-A"))
    b = compute_r2_path("s", "u", 0, _result(body=b"image-B"))
    assert a != b


def test_path_components_isolated() -> None:
    """season / chapter / panel_idx each contribute to the path independently."""
    base = compute_r2_path("s1", "u1", 0, _result(body=b"X"))
    assert compute_r2_path("s2", "u1", 0, _result(body=b"X")) != base
    assert compute_r2_path("s1", "u2", 0, _result(body=b"X")) != base
    assert compute_r2_path("s1", "u1", 1, _result(body=b"X")) != base


def test_hash_segment_is_16_hex_chars() -> None:
    path = compute_r2_path("s", "u", 0, _result(body=b"hi"))
    # last segment shape: "{idx}-{hash}.{ext}"
    filename = path.rsplit("/", 1)[1]
    hash_part = filename.split("-", 1)[1].split(".", 1)[0]
    assert len(hash_part) == 16
    int(hash_part, 16)  # raises if not valid hex
