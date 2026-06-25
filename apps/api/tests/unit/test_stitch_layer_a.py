"""Unit tests: stitch_pipeline.stitch_layer_a.

Delta 008.

ffmpeg is mocked at the asyncio.to_thread level (patching
_concat_layer_a_sync) so the suite runs without ffmpeg installed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from app.domain.stitch_pipeline import StitchError, StitchLayerAResult, stitch_layer_a
from app.infra.r2_uploader import R2Uploader, R2UploadError

_MODULE = "app.domain.stitch_pipeline"

_CHAPTER_UUID: UUID = UUID("11111111-2222-3333-4444-555566667777")
_SEASON_SLUG = "s01-el-tunel"
_R2_BASE = "https://r2.example.com"
_FAKE_MP4 = b"FAKEMP4DATA"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_uploader(base_url: str = _R2_BASE) -> R2Uploader:
    uploader = MagicMock(spec=R2Uploader)
    uploader.upload = AsyncMock(
        side_effect=lambda key, body, ct: f"{base_url}/{key}"
    )
    return uploader


def _write_mp4(path: Path, content: bytes = _FAKE_MP4) -> Path:
    path.write_bytes(content)
    return path


def _stub_concat(
    intro_mp4: Path,
    body_mp4: Path,
    outro_mp4: Path,
    out_path: Path,
) -> None:
    """Writes fake mp4 bytes to out_path without calling ffmpeg."""
    out_path.write_bytes(_FAKE_MP4)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stitch_layer_a_returns_result(tmp_path: Path) -> None:
    intro = _write_mp4(tmp_path / "intro.mp4")
    body = _write_mp4(tmp_path / "body.mp4")
    outro = _write_mp4(tmp_path / "outro.mp4")
    uploader = _make_uploader()

    with patch(f"{_MODULE}._concat_layer_a_sync", side_effect=_stub_concat):
        result = await stitch_layer_a(
            intro_mp4=intro,
            body_mp4=body,
            outro_mp4=outro,
            tmp_dir=tmp_path,
            uploader=uploader,
            season_slug=_SEASON_SLUG,
            chapter_public_id=_CHAPTER_UUID,
        )

    assert isinstance(result, StitchLayerAResult)
    assert result.video_duration_s == 14.0
    assert result.video_bytes_len == len(_FAKE_MP4)


@pytest.mark.asyncio
async def test_stitch_layer_a_video_url_from_r2(tmp_path: Path) -> None:
    intro = _write_mp4(tmp_path / "intro.mp4")
    body = _write_mp4(tmp_path / "body.mp4")
    outro = _write_mp4(tmp_path / "outro.mp4")
    uploader = _make_uploader()

    with patch(f"{_MODULE}._concat_layer_a_sync", side_effect=_stub_concat):
        result = await stitch_layer_a(
            intro_mp4=intro,
            body_mp4=body,
            outro_mp4=outro,
            tmp_dir=tmp_path,
            uploader=uploader,
            season_slug=_SEASON_SLUG,
            chapter_public_id=_CHAPTER_UUID,
        )

    assert result.video_url.startswith(_R2_BASE)
    assert _SEASON_SLUG in result.video_url
    assert str(_CHAPTER_UUID) in result.video_url
    assert result.video_url.endswith(".mp4")


@pytest.mark.asyncio
async def test_stitch_layer_a_r2_key_format(tmp_path: Path) -> None:
    intro = _write_mp4(tmp_path / "intro.mp4")
    body = _write_mp4(tmp_path / "body.mp4")
    outro = _write_mp4(tmp_path / "outro.mp4")
    uploader = _make_uploader()
    captured_keys: list[str] = []

    async def capture_upload(key: str, body: bytes, ct: str) -> str:
        captured_keys.append(key)
        return f"{_R2_BASE}/{key}"

    uploader.upload = AsyncMock(side_effect=capture_upload)

    with patch(f"{_MODULE}._concat_layer_a_sync", side_effect=_stub_concat):
        await stitch_layer_a(
            intro_mp4=intro,
            body_mp4=body,
            outro_mp4=outro,
            tmp_dir=tmp_path,
            uploader=uploader,
            season_slug=_SEASON_SLUG,
            chapter_public_id=_CHAPTER_UUID,
        )

    assert len(captured_keys) == 1
    key = captured_keys[0]
    # e.g. seasons/s01-el-tunel/<uuid>/chapter-<sha256[:8]>.mp4
    parts = key.split("/")
    assert parts[0] == "seasons"
    assert parts[1] == _SEASON_SLUG
    assert parts[2] == str(_CHAPTER_UUID)
    assert parts[3].startswith("chapter-")
    assert parts[3].endswith(".mp4")


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stitch_layer_a_ffmpeg_error_raises_stitch_error(tmp_path: Path) -> None:

    intro = _write_mp4(tmp_path / "intro.mp4")
    body = _write_mp4(tmp_path / "body.mp4")
    outro = _write_mp4(tmp_path / "outro.mp4")
    uploader = _make_uploader()

    def raise_ffmpeg(intro_mp4: Path, body_mp4: Path, outro_mp4: Path, out_path: Path) -> None:
        raise StitchError("ffmpeg layer-A concat failed: crashed")

    with (
        patch(f"{_MODULE}._concat_layer_a_sync", side_effect=raise_ffmpeg),
        pytest.raises(StitchError, match="ffmpeg layer-A concat failed"),
    ):
        await stitch_layer_a(
            intro_mp4=intro,
            body_mp4=body,
            outro_mp4=outro,
            tmp_dir=tmp_path,
            uploader=uploader,
            season_slug=_SEASON_SLUG,
            chapter_public_id=_CHAPTER_UUID,
        )


@pytest.mark.asyncio
async def test_stitch_layer_a_r2_upload_error_raises_stitch_error(tmp_path: Path) -> None:
    intro = _write_mp4(tmp_path / "intro.mp4")
    body = _write_mp4(tmp_path / "body.mp4")
    outro = _write_mp4(tmp_path / "outro.mp4")
    uploader = _make_uploader()
    uploader.upload = AsyncMock(side_effect=R2UploadError("S3 503"))

    with (
        patch(f"{_MODULE}._concat_layer_a_sync", side_effect=_stub_concat),
        pytest.raises(StitchError, match="R2 upload failed"),
    ):
        await stitch_layer_a(
            intro_mp4=intro,
            body_mp4=body,
            outro_mp4=outro,
            tmp_dir=tmp_path,
            uploader=uploader,
            season_slug=_SEASON_SLUG,
            chapter_public_id=_CHAPTER_UUID,
        )


@pytest.mark.asyncio
async def test_stitch_layer_a_empty_output_raises_stitch_error(tmp_path: Path) -> None:
    intro = _write_mp4(tmp_path / "intro.mp4")
    body = _write_mp4(tmp_path / "body.mp4")
    outro = _write_mp4(tmp_path / "outro.mp4")
    uploader = _make_uploader()

    def write_empty(intro_mp4: Path, body_mp4: Path, outro_mp4: Path, out_path: Path) -> None:
        out_path.write_bytes(b"")

    with (
        patch(f"{_MODULE}._concat_layer_a_sync", side_effect=write_empty),
        pytest.raises(StitchError, match="empty output"),
    ):
        await stitch_layer_a(
            intro_mp4=intro,
            body_mp4=body,
            outro_mp4=outro,
            tmp_dir=tmp_path,
            uploader=uploader,
            season_slug=_SEASON_SLUG,
            chapter_public_id=_CHAPTER_UUID,
        )
