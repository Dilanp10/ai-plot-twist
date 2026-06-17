"""Unit tests: clip_pipeline.render_clip — fake video router + mock uploader.

Module 008 / Task T-009 delta.

No real network or R2 calls. Tests use:
  - FakeVideoProvider inside a real VideoProviderRouter (check_health=False).
  - AsyncMock for R2Uploader.upload.
  - patch for app.domain.clip_pipeline.synthesize.

Coverage:
  ClipResult dataclass:
    - fields exist (idx, clip_url, clip_path, tts_path, duration_s,
                    provider_used, ok)

  AllClipsFailedError:
    - is subclass of Exception

  render_clip — happy path:
    - returns ClipResult with ok=True
    - clip_path is a file that exists in tmp_dir
    - clip_url is the R2 URL returned by uploader
    - provider_used matches the video provider
    - duration_s from VideoResult

  render_clip — TTS succeeds:
    - tts_path is a file that exists in tmp_dir

  render_clip — TTS returns None:
    - tts_path is None
    - ok remains True

  render_clip — VideoProviderError:
    - returns ClipResult with ok=False
    - provider_used == "placeholder"
    - clip_path file exists (placeholder bytes written)
    - clip_url == placeholder_video_url
    - duration_s == 0.0

  render_clip — R2UploadError on clip upload:
    - returns ClipResult with ok=False
    - clip_path file written with placeholder bytes

  render_clip — seed derivation:
    - seed passed to VideoRequest equals stable_hash(chapter_id, clip.idx)

  render_clip — tmp files:
    - clip tmp file is named clip_{idx}.mp4
    - audio tmp file is named audio_{idx}.mp3
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.domain.clip_pipeline import AllClipsFailedError, ClipResult, render_clip
from app.domain.scriptwriter_response import Clip
from app.domain.seed_derivation import stable_hash
from app.infra.r2_uploader import R2Uploader, R2UploadError
from app.providers.video import (
    MINIMAL_MP4,
    FakeVideoProvider,
    VideoProviderRouter,
)
from app.providers.video.base import VideoRequest, VideoResult

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHAPTER_ID = 7
_CHAPTER_PUBLIC_ID: UUID = uuid4()
_SEASON_SLUG = "s01-el-tunel"
_PLACEHOLDER_URL = "https://assets.example.com/static/placeholder.mp4"
_TTS_VOICE = "es-AR-ElenaNeural"

_GOOD_VISUAL = "a shattered mirror reflecting two timelines, cinematic 35mm"
_GOOD_NARRATION = "El espejo crujio como hielo viejo al amanecer."
_GOOD_TTS = "El espejo crujio como hielo viejo al amanecer."

_FAKE_MP3 = b"\xff\xfb\x90\x00" + b"\x00" * 100  # minimal mp3-like bytes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_clip(idx: int = 1) -> Clip:
    return Clip(
        idx=idx,
        narration=_GOOD_NARRATION,
        visual_prompt=_GOOD_VISUAL,
        mood="tense",
        tts_text=_GOOD_TTS,
    )


def _make_router(*, fail: bool = False) -> VideoProviderRouter:
    if fail:
        return VideoProviderRouter(providers=[], check_health=False)
    return VideoProviderRouter(
        providers=[FakeVideoProvider()],
        check_health=False,
        backoff_schedule_seconds=(0.0,),
    )


def _make_uploader(
    *,
    base_url: str = "https://r2.example.com",
    raise_error: bool = False,
) -> R2Uploader:
    uploader = MagicMock(spec=R2Uploader)
    if raise_error:
        uploader.upload = AsyncMock(side_effect=R2UploadError("bucket unreachable"))
    else:
        uploader.upload = AsyncMock(
            side_effect=lambda key, body, ct: f"{base_url}/{key}"
        )
    return uploader


async def _call_render(
    *,
    clip: Clip | None = None,
    router: VideoProviderRouter | None = None,
    uploader: R2Uploader | None = None,
    tts_bytes: bytes | None = _FAKE_MP3,
    tmp_dir: Path,
) -> ClipResult:
    with patch(
        "app.domain.clip_pipeline.synthesize",
        new=AsyncMock(return_value=tts_bytes),
    ):
        return await render_clip(
            clip=clip or _make_clip(),
            chapter_id=_CHAPTER_ID,
            chapter_public_id=_CHAPTER_PUBLIC_ID,
            season_slug=_SEASON_SLUG,
            video_router=router or _make_router(),
            uploader=uploader or _make_uploader(),
            tts_voice=_TTS_VOICE,
            placeholder_video_url=_PLACEHOLDER_URL,
            placeholder_bytes=MINIMAL_MP4,
            tmp_dir=tmp_dir,
            duration_s=5.0,
        )


# ---------------------------------------------------------------------------
# ClipResult and AllClipsFailedError types
# ---------------------------------------------------------------------------


def test_clip_result_is_dataclass() -> None:
    r = ClipResult(
        idx=1,
        clip_url="https://r2.example/1.mp4",
        clip_path="/tmp/clip_1.mp4",
        tts_path=None,
        duration_s=5.0,
        provider_used="hf",
        ok=True,
    )
    assert r.idx == 1
    assert r.ok is True


def test_all_clips_failed_error_is_exception() -> None:
    assert issubclass(AllClipsFailedError, Exception)
    err = AllClipsFailedError("test")
    assert isinstance(err, Exception)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_ok_true(tmp_path: Path) -> None:
    result = await _call_render(tmp_dir=tmp_path)
    assert result.ok is True


@pytest.mark.asyncio
async def test_happy_path_clip_file_exists(tmp_path: Path) -> None:
    result = await _call_render(tmp_dir=tmp_path)
    assert Path(result.clip_path).exists()


@pytest.mark.asyncio
async def test_happy_path_clip_path_in_tmp_dir(tmp_path: Path) -> None:
    result = await _call_render(tmp_dir=tmp_path)
    assert str(tmp_path) in result.clip_path


@pytest.mark.asyncio
async def test_happy_path_clip_url_from_uploader(tmp_path: Path) -> None:
    result = await _call_render(tmp_dir=tmp_path)
    assert result.clip_url.startswith("https://r2.example.com/")


@pytest.mark.asyncio
async def test_happy_path_provider_used(tmp_path: Path) -> None:
    result = await _call_render(tmp_dir=tmp_path)
    assert result.provider_used == "fake"


@pytest.mark.asyncio
async def test_happy_path_duration_s(tmp_path: Path) -> None:
    result = await _call_render(tmp_dir=tmp_path)
    assert result.duration_s == pytest.approx(5.0, abs=0.1)


@pytest.mark.asyncio
async def test_happy_path_idx_matches_clip(tmp_path: Path) -> None:
    result = await _call_render(clip=_make_clip(idx=3), tmp_dir=tmp_path)
    assert result.idx == 3


# ---------------------------------------------------------------------------
# TTS paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tts_success_path_exists(tmp_path: Path) -> None:
    result = await _call_render(tts_bytes=_FAKE_MP3, tmp_dir=tmp_path)
    assert result.tts_path is not None
    assert Path(result.tts_path).exists()


@pytest.mark.asyncio
async def test_tts_none_ok_remains_true(tmp_path: Path) -> None:
    result = await _call_render(tts_bytes=None, tmp_dir=tmp_path)
    assert result.tts_path is None
    assert result.ok is True


@pytest.mark.asyncio
async def test_tts_audio_file_named_correctly(tmp_path: Path) -> None:
    result = await _call_render(clip=_make_clip(idx=2), tts_bytes=_FAKE_MP3, tmp_dir=tmp_path)
    assert result.tts_path is not None
    assert Path(result.tts_path).name == "audio_2.mp3"


# ---------------------------------------------------------------------------
# VideoProviderError -> placeholder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_video_error_ok_false(tmp_path: Path) -> None:
    result = await _call_render(router=_make_router(fail=True), tmp_dir=tmp_path)
    assert result.ok is False


@pytest.mark.asyncio
async def test_video_error_provider_placeholder(tmp_path: Path) -> None:
    result = await _call_render(router=_make_router(fail=True), tmp_dir=tmp_path)
    assert result.provider_used == "placeholder"


@pytest.mark.asyncio
async def test_video_error_clip_url_is_placeholder(tmp_path: Path) -> None:
    result = await _call_render(router=_make_router(fail=True), tmp_dir=tmp_path)
    assert result.clip_url == _PLACEHOLDER_URL


@pytest.mark.asyncio
async def test_video_error_duration_zero(tmp_path: Path) -> None:
    result = await _call_render(router=_make_router(fail=True), tmp_dir=tmp_path)
    assert result.duration_s == 0.0


@pytest.mark.asyncio
async def test_video_error_clip_file_written(tmp_path: Path) -> None:
    result = await _call_render(router=_make_router(fail=True), tmp_dir=tmp_path)
    assert Path(result.clip_path).exists()
    assert Path(result.clip_path).read_bytes() == MINIMAL_MP4


@pytest.mark.asyncio
async def test_video_error_tts_path_none(tmp_path: Path) -> None:
    result = await _call_render(router=_make_router(fail=True), tmp_dir=tmp_path)
    assert result.tts_path is None


# ---------------------------------------------------------------------------
# R2UploadError -> placeholder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_r2_error_ok_false(tmp_path: Path) -> None:
    result = await _call_render(uploader=_make_uploader(raise_error=True), tmp_dir=tmp_path)
    assert result.ok is False


@pytest.mark.asyncio
async def test_r2_error_clip_file_has_placeholder_bytes(tmp_path: Path) -> None:
    result = await _call_render(uploader=_make_uploader(raise_error=True), tmp_dir=tmp_path)
    assert Path(result.clip_path).read_bytes() == MINIMAL_MP4


@pytest.mark.asyncio
async def test_r2_error_clip_url_is_placeholder(tmp_path: Path) -> None:
    result = await _call_render(uploader=_make_uploader(raise_error=True), tmp_dir=tmp_path)
    assert result.clip_url == _PLACEHOLDER_URL


# ---------------------------------------------------------------------------
# Seed derivation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_forwarded_to_router(tmp_path: Path) -> None:
    captured_seed: list[int] = []

    class _CaptureSeedProvider(FakeVideoProvider):
        async def generate(self, req: VideoRequest) -> VideoResult:
            captured_seed.append(req.seed)
            return await super().generate(req)

    router = VideoProviderRouter(
        providers=[_CaptureSeedProvider()],
        check_health=False,
        backoff_schedule_seconds=(0.0,),
    )
    await _call_render(clip=_make_clip(idx=2), router=router, tmp_dir=tmp_path)
    assert captured_seed == [stable_hash(_CHAPTER_ID, 2)]


# ---------------------------------------------------------------------------
# Tmp file naming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clip_tmp_file_name(tmp_path: Path) -> None:
    result = await _call_render(clip=_make_clip(idx=4), tmp_dir=tmp_path)
    assert Path(result.clip_path).name == "clip_4.mp4"


@pytest.mark.asyncio
async def test_r2_key_contains_season_and_chapter(tmp_path: Path) -> None:
    uploader = _make_uploader()
    await _call_render(uploader=uploader, tmp_dir=tmp_path)
    call_args = uploader.upload.call_args
    key = call_args.args[0]
    assert _SEASON_SLUG in key
    assert str(_CHAPTER_PUBLIC_ID) in key
