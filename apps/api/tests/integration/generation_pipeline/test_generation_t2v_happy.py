"""T2V happy path: video router renders all clips, ffmpeg stitches → v2.0 manifest.

Module 008 / Task T-010 delta.

ffmpeg is mocked at the stitch_pipeline level so the suite runs without
the binary installed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.generation_pipeline import run_generation_pipeline
from app.domain.stitch_pipeline import StitchResult

from ._helpers import (
    CHAPTER_ID,
    PLACEHOLDER_URL,
    PLACEHOLDER_VIDEO_BYTES,
    PLACEHOLDER_VIDEO_URL,
    TTS_VOICE,
    make_ctx,
    make_image_router,
    make_mock_session,
    make_script,
    make_scriptwriter,
    make_uploader,
    make_video_router,
)

_MODULE = "app.domain.generation_pipeline"
_NEW_CHAPTER_DB_ID = 70


def _stub_stitch_result() -> StitchResult:
    return StitchResult(
        video_url="https://r2.example.com/chapter-ab12cd34.mp4",
        video_duration_s=20.0,
        video_bytes_len=4096,
    )


@pytest.mark.asyncio
async def test_t2v_happy_status_ready(tmp_path: Path) -> None:
    script = make_script(n_clips=4)
    ctx = make_ctx()
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
        patch(
            f"{_MODULE}.stitch_clips",
            new=AsyncMock(return_value=_stub_stitch_result()),
        ),
        patch("app.domain.clip_pipeline.synthesize", new=AsyncMock(return_value=None)),
    ):
        await run_generation_pipeline(
            CHAPTER_ID,
            session=make_mock_session(),
            scriptwriter=make_scriptwriter(script),
            image_router=make_image_router(),
            uploader=make_uploader(),
            placeholder_url=PLACEHOLDER_URL,
            tts_voice=TTS_VOICE,
            panel_concurrency=4,
            deadline_s=60.0,
            video_router=make_video_router(),
            placeholder_video_url=PLACEHOLDER_VIDEO_URL,
            placeholder_video_bytes=PLACEHOLDER_VIDEO_BYTES,
            clip_concurrency=4,
            video_pipeline_enabled=True,
        )

    assert persist_mock.call_args.kwargs["status"] == "ready"


@pytest.mark.asyncio
async def test_t2v_happy_manifest_is_video_mp4() -> None:
    script = make_script(n_clips=4)
    ctx = make_ctx()
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
        patch(
            f"{_MODULE}.stitch_clips",
            new=AsyncMock(return_value=_stub_stitch_result()),
        ),
        patch("app.domain.clip_pipeline.synthesize", new=AsyncMock(return_value=None)),
    ):
        await run_generation_pipeline(
            CHAPTER_ID,
            session=make_mock_session(),
            scriptwriter=make_scriptwriter(script),
            image_router=make_image_router(),
            uploader=make_uploader(),
            placeholder_url=PLACEHOLDER_URL,
            tts_voice=TTS_VOICE,
            panel_concurrency=4,
            deadline_s=60.0,
            video_router=make_video_router(),
            placeholder_video_url=PLACEHOLDER_VIDEO_URL,
            placeholder_video_bytes=PLACEHOLDER_VIDEO_BYTES,
        )

    manifest = persist_mock.call_args.kwargs["manifest"]
    assert manifest["schema_version"] == "2.0"
    assert manifest["manifest_kind"] == "video_mp4"
    assert "video_url" in manifest
    assert "clips" in manifest
    assert "panels" not in manifest


@pytest.mark.asyncio
async def test_t2v_happy_manifest_video_url_from_stitch() -> None:
    script = make_script(n_clips=4)
    ctx = make_ctx()
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
        patch(
            f"{_MODULE}.stitch_clips",
            new=AsyncMock(return_value=_stub_stitch_result()),
        ),
        patch("app.domain.clip_pipeline.synthesize", new=AsyncMock(return_value=None)),
    ):
        await run_generation_pipeline(
            CHAPTER_ID,
            session=make_mock_session(),
            scriptwriter=make_scriptwriter(script),
            image_router=make_image_router(),
            uploader=make_uploader(),
            placeholder_url=PLACEHOLDER_URL,
            tts_voice=TTS_VOICE,
            panel_concurrency=4,
            deadline_s=60.0,
            video_router=make_video_router(),
            placeholder_video_url=PLACEHOLDER_VIDEO_URL,
            placeholder_video_bytes=PLACEHOLDER_VIDEO_BYTES,
        )

    manifest = persist_mock.call_args.kwargs["manifest"]
    assert manifest["video_url"] == "https://r2.example.com/chapter-ab12cd34.mp4"
    assert manifest["video_duration_s"] == 20.0


@pytest.mark.asyncio
async def test_t2v_happy_returns_video_kind_summary() -> None:
    script = make_script(n_clips=4)
    ctx = make_ctx()

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=AsyncMock(return_value=_NEW_CHAPTER_DB_ID)),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
        patch(
            f"{_MODULE}.stitch_clips",
            new=AsyncMock(return_value=_stub_stitch_result()),
        ),
        patch("app.domain.clip_pipeline.synthesize", new=AsyncMock(return_value=None)),
    ):
        summary = await run_generation_pipeline(
            CHAPTER_ID,
            session=make_mock_session(),
            scriptwriter=make_scriptwriter(script),
            image_router=make_image_router(),
            uploader=make_uploader(),
            placeholder_url=PLACEHOLDER_URL,
            tts_voice=TTS_VOICE,
            panel_concurrency=4,
            deadline_s=60.0,
            video_router=make_video_router(),
            placeholder_video_url=PLACEHOLDER_VIDEO_URL,
            placeholder_video_bytes=PLACEHOLDER_VIDEO_BYTES,
        )

    assert summary.manifest_kind == "video_mp4"
    assert summary.panels_ok == 4  # 4 clips ok
    assert summary.panels_degraded == 0
