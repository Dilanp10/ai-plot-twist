"""Unit tests: compute_r2_clip_path.

Module 012 / Task T-006.
"""

from __future__ import annotations

import hashlib

from app.providers.video.base import VideoResult
from app.providers.video.fake import MINIMAL_MP4
from app.providers.video.paths import compute_r2_clip_path


def _result(data: bytes = MINIMAL_MP4) -> VideoResult:
    return VideoResult(
        bytes_=data,
        mime_type="video/mp4",
        provider="fake",
        model="fake",
        duration_s=5.0,
        frames_count=121,
        latency_ms=0,
    )


def test_path_format() -> None:
    path = compute_r2_clip_path("s01", "chapter-uuid", 0, _result())
    assert path.startswith("seasons/s01/chapter-uuid/clips/0-")
    assert path.endswith(".mp4")


def test_hash_is_sha256_prefix() -> None:
    data = b"some clip bytes"
    expected_hash = hashlib.sha256(data).hexdigest()[:8]
    path = compute_r2_clip_path("slug", "uuid", 2, _result(data))
    assert expected_hash in path


def test_same_bytes_same_path() -> None:
    r = _result(MINIMAL_MP4)
    assert compute_r2_clip_path("s", "u", 0, r) == compute_r2_clip_path("s", "u", 0, r)


def test_different_bytes_different_path() -> None:
    p1 = compute_r2_clip_path("s", "u", 0, _result(b"clip_a"))
    p2 = compute_r2_clip_path("s", "u", 0, _result(b"clip_b"))
    assert p1 != p2


def test_clip_idx_in_path() -> None:
    for idx in (0, 1, 3, 5):
        path = compute_r2_clip_path("s", "u", idx, _result())
        assert f"/{idx}-" in path


def test_season_slug_and_chapter_in_path() -> None:
    path = compute_r2_clip_path("temporada-01", "abc-def", 0, _result())
    assert "temporada-01" in path
    assert "abc-def" in path
