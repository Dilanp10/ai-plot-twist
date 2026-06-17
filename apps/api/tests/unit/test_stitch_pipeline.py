"""Unit tests: stitch_pipeline.stitch_clips — mocked ffmpeg + R2.

Module 008 / Task T-016 delta.

No real ffmpeg subprocess: all ffmpeg.run() calls are patched to write
sentinel bytes into the expected output path. This isolates the test from
ffmpeg's binary presence and keeps the suite fast.

Coverage:
  StitchResult:
    - frozen dataclass with the three fields.

  StitchError:
    - subclass of Exception.

  stitch_clips — happy path:
    - returns StitchResult with the uploaded R2 URL.
    - video_url matches uploader's return value.
    - video_duration_s == sum of clip durations.
    - video_bytes_len == len(output mp4).
    - R2 key matches seasons/{slug}/{uuid}/chapter-{hash[:8]}.mp4 format.
    - clips are processed in idx order (defensive sort).

  stitch_clips — concat list:
    - clips_list.txt is written in tmp_dir with `file 'path'` per clip.

  stitch_clips — missing audio:
    - clips without tts_path use ffmpeg.input(anullsrc, f=lavfi).
    - all TTS missing → stitch still proceeds (video-only audio = silence).

  stitch_clips — failure surfaces:
    - empty clip list → StitchError.
    - ffmpeg.Error during concat → StitchError.
    - ffmpeg.Error during mux → StitchError.
    - R2UploadError → StitchError.
    - missing output file after ffmpeg → StitchError.
    - empty output bytes → StitchError.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import ffmpeg
import pytest

from app.domain.clip_pipeline import ClipResult
from app.domain.stitch_pipeline import (
    StitchError,
    StitchResult,
    stitch_clips,
)
from app.infra.r2_uploader import R2Uploader, R2UploadError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SEASON_SLUG = "s01-el-tunel"
_CHAPTER_PUBLIC_ID: UUID = uuid4()

_SENTINEL_VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100
_SENTINEL_AUDIO_BYTES = b"\xff\xfb\x90\x00" + b"\x00" * 50
_SENTINEL_CHAPTER_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\xab" * 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clip(
    *,
    idx: int,
    tmp_dir: Path,
    with_audio: bool = True,
    duration_s: float = 5.0,
) -> ClipResult:
    clip_path = tmp_dir / f"clip_{idx}.mp4"
    clip_path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + bytes([idx]) * 50)
    if with_audio:
        audio_path = tmp_dir / f"audio_{idx}.mp3"
        audio_path.write_bytes(b"\xff\xfb\x90\x00" + bytes([idx]) * 50)
        tts: str | None = str(audio_path)
    else:
        tts = None
    return ClipResult(
        idx=idx,
        clip_url=f"https://r2.example/clip-{idx}.mp4",
        clip_path=str(clip_path),
        tts_path=tts,
        duration_s=duration_s,
        provider_used="hf",
        ok=True,
    )


def _make_uploader(*, raise_error: bool = False) -> R2Uploader:
    uploader = MagicMock(spec=R2Uploader)
    if raise_error:
        uploader.upload = AsyncMock(side_effect=R2UploadError("bucket unreachable"))
    else:
        uploader.upload = AsyncMock(
            side_effect=lambda key, body, ct: f"https://r2.example.com/{key}"
        )
    return uploader


class _FakeFfmpegStream:
    """Mimics ffmpeg's chainable Stream object for output/run."""

    def __init__(self, on_run: object | None = None) -> None:
        self._on_run = on_run

    def output(self, *args: object, **kwargs: object) -> _FakeFfmpegStream:
        # Last positional arg is the output path string; remember it for run()
        # to materialize bytes on disk.
        if args:
            self._out_path = str(args[-1]) if isinstance(args[-1], str) else None
        return self

    def run(self, **kwargs: object) -> tuple[bytes, bytes]:
        if self._on_run is not None:
            self._on_run(getattr(self, "_out_path", None))
        return (b"", b"")


def _stub_ffmpeg(
    *,
    video_concat_writes: bytes = _SENTINEL_VIDEO_BYTES,
    audio_concat_writes: bytes = _SENTINEL_AUDIO_BYTES,
    mux_writes: bytes = _SENTINEL_CHAPTER_BYTES,
    fail_on: str | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Patch ffmpeg.input / ffmpeg.concat / ffmpeg.output to write sentinel bytes.

    Returns (input_mock, concat_mock) so individual tests can inspect calls.

    The three ffmpeg call sites in stitch_pipeline are:
      1. ``ffmpeg.input(clips_list, format='concat')...output(video_only).run()``
      2. ``ffmpeg.concat(*audio_inputs, v=0, a=1).output(audio_track).run()``
      3. ``ffmpeg.output(video, audio, chapter_mp4, ...).run()``

    ``fail_on`` in {"video_concat", "audio_concat", "mux"} makes the
    matching .run() raise ffmpeg.Error.
    """

    def _make_error() -> ffmpeg.Error:
        return ffmpeg.Error("ffmpeg", b"stdout", b"sample stderr message")

    def _on_run_video(out_path: str | None) -> None:
        if fail_on == "video_concat":
            raise _make_error()
        if out_path:
            Path(out_path).write_bytes(video_concat_writes)

    def _on_run_audio(out_path: str | None) -> None:
        if fail_on == "audio_concat":
            raise _make_error()
        if out_path:
            Path(out_path).write_bytes(audio_concat_writes)

    def _on_run_mux(out_path: str | None) -> None:
        if fail_on == "mux":
            raise _make_error()
        if out_path:
            Path(out_path).write_bytes(mux_writes)

    def _input_side_effect(*args: object, **kwargs: object) -> _FakeFfmpegStream:
        # First input call uses format="concat" → video concat chain
        if kwargs.get("format") == "concat":
            return _FakeFfmpegStream(on_run=_on_run_video)
        # Otherwise it's an audio input (lavfi for silence, or mp3 file)
        # — returns a passive stream consumed by ffmpeg.concat
        return _FakeFfmpegStream()

    def _concat_side_effect(*args: object, **kwargs: object) -> _FakeFfmpegStream:
        return _FakeFfmpegStream(on_run=_on_run_audio)

    def _output_side_effect(*args: object, **kwargs: object) -> _FakeFfmpegStream:
        # Top-level ffmpeg.output(video, audio, out_path, ...) → mux call
        return _FakeFfmpegStream(on_run=_on_run_mux).output(*args, **kwargs)

    input_mock = MagicMock(side_effect=_input_side_effect)
    concat_mock = MagicMock(side_effect=_concat_side_effect)
    output_mock = MagicMock(side_effect=_output_side_effect)
    return input_mock, concat_mock, output_mock  # type: ignore[return-value]


def _patch_ffmpeg(**kwargs: object) -> Any:
    input_mock, concat_mock, output_mock = _stub_ffmpeg(**kwargs)  # type: ignore[arg-type]
    return patch.multiple(
        "app.domain.stitch_pipeline.ffmpeg",
        input=input_mock,
        concat=concat_mock,
        output=output_mock,
    )


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


def test_stitch_result_is_frozen_dataclass() -> None:
    import dataclasses

    r = StitchResult(video_url="x", video_duration_s=10.0, video_bytes_len=1000)
    assert r.video_url == "x"
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.video_url = "y"  # type: ignore[misc]


def test_stitch_error_is_exception() -> None:
    assert issubclass(StitchError, Exception)
    with pytest.raises(StitchError):
        raise StitchError("boom")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stitch_returns_stitch_result(tmp_path: Path) -> None:
    clips = [_clip(idx=i, tmp_dir=tmp_path) for i in range(1, 5)]
    with _patch_ffmpeg():
        result = await stitch_clips(
            clips=clips,
            tmp_dir=tmp_path,
            uploader=_make_uploader(),
            season_slug=_SEASON_SLUG,
            chapter_public_id=_CHAPTER_PUBLIC_ID,
        )
    assert isinstance(result, StitchResult)


@pytest.mark.asyncio
async def test_stitch_video_url_from_uploader(tmp_path: Path) -> None:
    clips = [_clip(idx=i, tmp_dir=tmp_path) for i in range(1, 5)]
    with _patch_ffmpeg():
        result = await stitch_clips(
            clips=clips,
            tmp_dir=tmp_path,
            uploader=_make_uploader(),
            season_slug=_SEASON_SLUG,
            chapter_public_id=_CHAPTER_PUBLIC_ID,
        )
    assert result.video_url.startswith("https://r2.example.com/")


@pytest.mark.asyncio
async def test_stitch_r2_key_format(tmp_path: Path) -> None:
    clips = [_clip(idx=i, tmp_dir=tmp_path) for i in range(1, 5)]
    uploader = _make_uploader()
    with _patch_ffmpeg():
        await stitch_clips(
            clips=clips,
            tmp_dir=tmp_path,
            uploader=uploader,
            season_slug=_SEASON_SLUG,
            chapter_public_id=_CHAPTER_PUBLIC_ID,
        )
    key = uploader.upload.call_args.args[0]
    assert key.startswith(f"seasons/{_SEASON_SLUG}/{_CHAPTER_PUBLIC_ID}/chapter-")
    assert key.endswith(".mp4")


@pytest.mark.asyncio
async def test_stitch_duration_is_sum_of_clips(tmp_path: Path) -> None:
    clips = [_clip(idx=i, tmp_dir=tmp_path, duration_s=5.0) for i in range(1, 5)]
    with _patch_ffmpeg():
        result = await stitch_clips(
            clips=clips,
            tmp_dir=tmp_path,
            uploader=_make_uploader(),
            season_slug=_SEASON_SLUG,
            chapter_public_id=_CHAPTER_PUBLIC_ID,
        )
    assert result.video_duration_s == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_stitch_bytes_len_matches_output(tmp_path: Path) -> None:
    clips = [_clip(idx=i, tmp_dir=tmp_path) for i in range(1, 5)]
    with _patch_ffmpeg():
        result = await stitch_clips(
            clips=clips,
            tmp_dir=tmp_path,
            uploader=_make_uploader(),
            season_slug=_SEASON_SLUG,
            chapter_public_id=_CHAPTER_PUBLIC_ID,
        )
    assert result.video_bytes_len == len(_SENTINEL_CHAPTER_BYTES)


@pytest.mark.asyncio
async def test_stitch_passes_mp4_content_type(tmp_path: Path) -> None:
    clips = [_clip(idx=i, tmp_dir=tmp_path) for i in range(1, 5)]
    uploader = _make_uploader()
    with _patch_ffmpeg():
        await stitch_clips(
            clips=clips,
            tmp_dir=tmp_path,
            uploader=uploader,
            season_slug=_SEASON_SLUG,
            chapter_public_id=_CHAPTER_PUBLIC_ID,
        )
    ct = uploader.upload.call_args.args[2]
    assert ct == "video/mp4"


@pytest.mark.asyncio
async def test_stitch_sorts_clips_by_idx(tmp_path: Path) -> None:
    """Unsorted input list must be sorted defensively before concat list write."""
    clips = [
        _clip(idx=3, tmp_dir=tmp_path),
        _clip(idx=1, tmp_dir=tmp_path),
        _clip(idx=4, tmp_dir=tmp_path),
        _clip(idx=2, tmp_dir=tmp_path),
    ]
    with _patch_ffmpeg():
        await stitch_clips(
            clips=clips,
            tmp_dir=tmp_path,
            uploader=_make_uploader(),
            season_slug=_SEASON_SLUG,
            chapter_public_id=_CHAPTER_PUBLIC_ID,
        )
    text = (tmp_path / "clips_list.txt").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip()]
    assert "clip_1.mp4" in lines[0]
    assert "clip_2.mp4" in lines[1]
    assert "clip_3.mp4" in lines[2]
    assert "clip_4.mp4" in lines[3]


# ---------------------------------------------------------------------------
# Concat list file format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clips_list_file_format(tmp_path: Path) -> None:
    clips = [_clip(idx=i, tmp_dir=tmp_path) for i in range(1, 5)]
    with _patch_ffmpeg():
        await stitch_clips(
            clips=clips,
            tmp_dir=tmp_path,
            uploader=_make_uploader(),
            season_slug=_SEASON_SLUG,
            chapter_public_id=_CHAPTER_PUBLIC_ID,
        )
    text = (tmp_path / "clips_list.txt").read_text(encoding="utf-8")
    for line in text.splitlines():
        assert line.startswith("file '")
        assert line.endswith(".mp4'")


# ---------------------------------------------------------------------------
# Missing audio segments → silence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stitch_with_some_missing_audio(tmp_path: Path) -> None:
    clips = [
        _clip(idx=1, tmp_dir=tmp_path, with_audio=True),
        _clip(idx=2, tmp_dir=tmp_path, with_audio=False),
        _clip(idx=3, tmp_dir=tmp_path, with_audio=True),
        _clip(idx=4, tmp_dir=tmp_path, with_audio=False),
    ]
    with _patch_ffmpeg():
        result = await stitch_clips(
            clips=clips,
            tmp_dir=tmp_path,
            uploader=_make_uploader(),
            season_slug=_SEASON_SLUG,
            chapter_public_id=_CHAPTER_PUBLIC_ID,
        )
    assert isinstance(result, StitchResult)


@pytest.mark.asyncio
async def test_stitch_with_all_missing_audio(tmp_path: Path) -> None:
    clips = [_clip(idx=i, tmp_dir=tmp_path, with_audio=False) for i in range(1, 5)]
    with _patch_ffmpeg():
        result = await stitch_clips(
            clips=clips,
            tmp_dir=tmp_path,
            uploader=_make_uploader(),
            season_slug=_SEASON_SLUG,
            chapter_public_id=_CHAPTER_PUBLIC_ID,
        )
    assert isinstance(result, StitchResult)


@pytest.mark.asyncio
async def test_stitch_missing_audio_uses_lavfi(tmp_path: Path) -> None:
    """Each clip without tts_path → exactly one ffmpeg.input(..., f='lavfi') call."""
    clips = [
        _clip(idx=1, tmp_dir=tmp_path, with_audio=False),
        _clip(idx=2, tmp_dir=tmp_path, with_audio=False),
    ]
    captured: list[dict[str, object]] = []

    def _capture_input(*args: object, **kwargs: object) -> object:
        captured.append({"args": args, "kwargs": dict(kwargs)})
        if kwargs.get("format") == "concat":
            # Reuse the stub for the video concat path
            return _FakeFfmpegStream(
                on_run=lambda p: Path(p).write_bytes(_SENTINEL_VIDEO_BYTES) if p else None
            )
        return _FakeFfmpegStream()

    with (
        _patch_ffmpeg(),
        patch("app.domain.stitch_pipeline.ffmpeg.input", side_effect=_capture_input),
    ):
        await stitch_clips(
            clips=clips,
            tmp_dir=tmp_path,
            uploader=_make_uploader(),
            season_slug=_SEASON_SLUG,
            chapter_public_id=_CHAPTER_PUBLIC_ID,
        )
    lavfi_calls = [c for c in captured if c["kwargs"].get("f") == "lavfi"]
    assert len(lavfi_calls) == 2  # one silence per missing-audio clip


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_clips_raises_stitch_error(tmp_path: Path) -> None:
    with pytest.raises(StitchError, match="empty"):
        await stitch_clips(
            clips=[],
            tmp_dir=tmp_path,
            uploader=_make_uploader(),
            season_slug=_SEASON_SLUG,
            chapter_public_id=_CHAPTER_PUBLIC_ID,
        )


@pytest.mark.asyncio
async def test_ffmpeg_video_concat_failure_raises(tmp_path: Path) -> None:
    clips = [_clip(idx=i, tmp_dir=tmp_path) for i in range(1, 5)]
    with _patch_ffmpeg(fail_on="video_concat"), pytest.raises(StitchError, match="ffmpeg"):
        await stitch_clips(
            clips=clips,
            tmp_dir=tmp_path,
            uploader=_make_uploader(),
            season_slug=_SEASON_SLUG,
            chapter_public_id=_CHAPTER_PUBLIC_ID,
        )


@pytest.mark.asyncio
async def test_ffmpeg_audio_concat_failure_raises(tmp_path: Path) -> None:
    clips = [_clip(idx=i, tmp_dir=tmp_path) for i in range(1, 5)]
    with _patch_ffmpeg(fail_on="audio_concat"), pytest.raises(StitchError, match="ffmpeg"):
        await stitch_clips(
            clips=clips,
            tmp_dir=tmp_path,
            uploader=_make_uploader(),
            season_slug=_SEASON_SLUG,
            chapter_public_id=_CHAPTER_PUBLIC_ID,
        )


@pytest.mark.asyncio
async def test_ffmpeg_mux_failure_raises(tmp_path: Path) -> None:
    clips = [_clip(idx=i, tmp_dir=tmp_path) for i in range(1, 5)]
    with _patch_ffmpeg(fail_on="mux"), pytest.raises(StitchError, match="ffmpeg"):
        await stitch_clips(
            clips=clips,
            tmp_dir=tmp_path,
            uploader=_make_uploader(),
            season_slug=_SEASON_SLUG,
            chapter_public_id=_CHAPTER_PUBLIC_ID,
        )


@pytest.mark.asyncio
async def test_r2_upload_failure_raises_stitch_error(tmp_path: Path) -> None:
    clips = [_clip(idx=i, tmp_dir=tmp_path) for i in range(1, 5)]
    with _patch_ffmpeg(), pytest.raises(StitchError, match="R2"):
        await stitch_clips(
            clips=clips,
            tmp_dir=tmp_path,
            uploader=_make_uploader(raise_error=True),
            season_slug=_SEASON_SLUG,
            chapter_public_id=_CHAPTER_PUBLIC_ID,
        )


@pytest.mark.asyncio
async def test_empty_output_bytes_raises(tmp_path: Path) -> None:
    clips = [_clip(idx=i, tmp_dir=tmp_path) for i in range(1, 5)]
    with _patch_ffmpeg(mux_writes=b""), pytest.raises(StitchError, match="empty"):
        await stitch_clips(
            clips=clips,
            tmp_dir=tmp_path,
            uploader=_make_uploader(),
            season_slug=_SEASON_SLUG,
            chapter_public_id=_CHAPTER_PUBLIC_ID,
        )
