"""Layer A failure → coordinator falls through to Layer B (T2V) or Layer C (T2I).

Delta 008.

Tests that any exception during Layer A (render_intro, run_i2v,
stitch_layer_a, draft_v3) causes the coordinator to fall back gracefully.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.generation_pipeline import run_generation_pipeline
from app.domain.intro_overlay import IntroRenderError
from app.domain.stitch_pipeline import StitchError, StitchResult

from ._helpers import (
    CHAPTER_ID,
    PLACEHOLDER_URL,
    PLACEHOLDER_VIDEO_BYTES,
    PLACEHOLDER_VIDEO_URL,
    R2_PUBLIC_BASE_URL,
    TTS_VOICE,
    make_ctx_i2v,
    make_i2v_router,
    make_image_router,
    make_mock_session,
    make_script,
    make_script_v3,
    make_scriptwriter,
    make_stub_i2v_body_result,
    make_stub_stitch_layer_a_result,
    make_uploader,
    make_video_router,
)

_MODULE = "app.domain.generation_pipeline"
_NEW_CHAPTER_DB_ID = 81

_T2V_STITCH = StitchResult(
    video_url="https://r2.example.com/chapter-t2v.mp4",
    video_duration_s=20.0,
    video_bytes_len=1024,
)


def _make_intro_bg_outro(tmp_path: Path) -> tuple[Path, Path]:
    intro_bg = tmp_path / "intro_bg.png"
    intro_bg.write_bytes(b"\x89PNG")
    outro = tmp_path / "outro.mp4"
    outro.write_bytes(b"FAKEOUTRO")
    return intro_bg, outro


@pytest.mark.asyncio
async def test_render_intro_error_falls_back_to_t2v(tmp_path: Path) -> None:
    ctx = make_ctx_i2v()
    script_v3 = make_script_v3()
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)
    intro_bg, outro = _make_intro_bg_outro(tmp_path)

    i2v_body = make_stub_i2v_body_result(tmp_path)
    stitch_a = make_stub_stitch_layer_a_result()
    render_intro_err = AsyncMock(side_effect=IntroRenderError("ffmpeg crashed"))
    draft_v3_mock = AsyncMock(return_value=script_v3)

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
        patch(f"{_MODULE}.run_i2v", new=AsyncMock(return_value=i2v_body)),
        patch(f"{_MODULE}.render_intro", new=render_intro_err),
        patch(f"{_MODULE}.stitch_layer_a", new=AsyncMock(return_value=stitch_a)),
        patch(f"{_MODULE}.stitch_clips", new=AsyncMock(return_value=_T2V_STITCH)),
        patch("app.domain.scriptwriter.Scriptwriter.draft_v3", new=draft_v3_mock),
        patch("app.domain.clip_pipeline.synthesize", new=AsyncMock(return_value=None)),
    ):
        await run_generation_pipeline(
            CHAPTER_ID,
            session=make_mock_session(),
            scriptwriter=make_scriptwriter(make_script()),
            image_router=make_image_router(),
            uploader=make_uploader(),
            placeholder_url=PLACEHOLDER_URL,
            tts_voice=TTS_VOICE,
            panel_concurrency=4,
            deadline_s=60.0,
            video_router=make_video_router(),
            placeholder_video_url=PLACEHOLDER_VIDEO_URL,
            placeholder_video_bytes=PLACEHOLDER_VIDEO_BYTES,
            i2v_router=make_i2v_router(),
            intro_bg_path=intro_bg,
            outro_path=outro,
            r2_public_base_url=R2_PUBLIC_BASE_URL,
        )

    manifest = persist_mock.call_args.kwargs["manifest"]
    assert manifest["manifest_kind"] == "video_mp4"


@pytest.mark.asyncio
async def test_stitch_layer_a_error_falls_back_to_t2v(tmp_path: Path) -> None:
    ctx = make_ctx_i2v()
    script_v3 = make_script_v3()
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)
    intro_bg, outro = _make_intro_bg_outro(tmp_path)

    i2v_body = make_stub_i2v_body_result(tmp_path)
    stitch_a_err = AsyncMock(side_effect=StitchError("concat failed"))
    draft_v3_mock = AsyncMock(return_value=script_v3)

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
        patch(f"{_MODULE}.run_i2v", new=AsyncMock(return_value=i2v_body)),
        patch(f"{_MODULE}.render_intro", new=AsyncMock()),
        patch(f"{_MODULE}.stitch_layer_a", new=stitch_a_err),
        patch(f"{_MODULE}.stitch_clips", new=AsyncMock(return_value=_T2V_STITCH)),
        patch("app.domain.scriptwriter.Scriptwriter.draft_v3", new=draft_v3_mock),
        patch("app.domain.clip_pipeline.synthesize", new=AsyncMock(return_value=None)),
    ):
        await run_generation_pipeline(
            CHAPTER_ID,
            session=make_mock_session(),
            scriptwriter=make_scriptwriter(make_script()),
            image_router=make_image_router(),
            uploader=make_uploader(),
            placeholder_url=PLACEHOLDER_URL,
            tts_voice=TTS_VOICE,
            panel_concurrency=4,
            deadline_s=60.0,
            video_router=make_video_router(),
            placeholder_video_url=PLACEHOLDER_VIDEO_URL,
            placeholder_video_bytes=PLACEHOLDER_VIDEO_BYTES,
            i2v_router=make_i2v_router(),
            intro_bg_path=intro_bg,
            outro_path=outro,
            r2_public_base_url=R2_PUBLIC_BASE_URL,
        )

    manifest = persist_mock.call_args.kwargs["manifest"]
    assert manifest["manifest_kind"] == "video_mp4"


@pytest.mark.asyncio
async def test_i2v_provider_exhausted_falls_back_to_t2v(tmp_path: Path) -> None:
    ctx = make_ctx_i2v()
    script_v3 = make_script_v3()
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)
    intro_bg, outro = _make_intro_bg_outro(tmp_path)

    run_i2v_err = AsyncMock(side_effect=Exception("I2V provider failed"))
    stitch_a = make_stub_stitch_layer_a_result()
    draft_v3_mock = AsyncMock(return_value=script_v3)

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
        patch(f"{_MODULE}.run_i2v", new=run_i2v_err),
        patch(f"{_MODULE}.render_intro", new=AsyncMock()),
        patch(f"{_MODULE}.stitch_layer_a", new=AsyncMock(return_value=stitch_a)),
        patch(f"{_MODULE}.stitch_clips", new=AsyncMock(return_value=_T2V_STITCH)),
        patch("app.domain.scriptwriter.Scriptwriter.draft_v3", new=draft_v3_mock),
        patch("app.domain.clip_pipeline.synthesize", new=AsyncMock(return_value=None)),
    ):
        await run_generation_pipeline(
            CHAPTER_ID,
            session=make_mock_session(),
            scriptwriter=make_scriptwriter(make_script()),
            image_router=make_image_router(),
            uploader=make_uploader(),
            placeholder_url=PLACEHOLDER_URL,
            tts_voice=TTS_VOICE,
            panel_concurrency=4,
            deadline_s=60.0,
            video_router=make_video_router(),
            placeholder_video_url=PLACEHOLDER_VIDEO_URL,
            placeholder_video_bytes=PLACEHOLDER_VIDEO_BYTES,
            i2v_router=make_i2v_router(),
            intro_bg_path=intro_bg,
            outro_path=outro,
            r2_public_base_url=R2_PUBLIC_BASE_URL,
        )

    manifest = persist_mock.call_args.kwargs["manifest"]
    assert manifest["manifest_kind"] == "video_mp4"


@pytest.mark.asyncio
async def test_layer_a_fallback_does_not_crash_layer_b(tmp_path: Path) -> None:
    """Layer A exception is swallowed; Layer B runs and persists successfully."""
    ctx = make_ctx_i2v()
    script_v3 = make_script_v3()
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)
    stitch_clips_mock = AsyncMock(return_value=_T2V_STITCH)
    intro_bg, outro = _make_intro_bg_outro(tmp_path)

    i2v_body = make_stub_i2v_body_result(tmp_path)
    stitch_a = make_stub_stitch_layer_a_result()
    render_intro_err = AsyncMock(side_effect=IntroRenderError("ffmpeg crashed"))
    draft_v3_mock = AsyncMock(return_value=script_v3)

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
        patch(f"{_MODULE}.run_i2v", new=AsyncMock(return_value=i2v_body)),
        patch(f"{_MODULE}.render_intro", new=render_intro_err),
        patch(f"{_MODULE}.stitch_layer_a", new=AsyncMock(return_value=stitch_a)),
        patch(f"{_MODULE}.stitch_clips", new=stitch_clips_mock),
        patch("app.domain.scriptwriter.Scriptwriter.draft_v3", new=draft_v3_mock),
        patch("app.domain.clip_pipeline.synthesize", new=AsyncMock(return_value=None)),
    ):
        await run_generation_pipeline(
            CHAPTER_ID,
            session=make_mock_session(),
            scriptwriter=make_scriptwriter(make_script()),
            image_router=make_image_router(),
            uploader=make_uploader(),
            placeholder_url=PLACEHOLDER_URL,
            tts_voice=TTS_VOICE,
            panel_concurrency=4,
            deadline_s=60.0,
            video_router=make_video_router(),
            placeholder_video_url=PLACEHOLDER_VIDEO_URL,
            placeholder_video_bytes=PLACEHOLDER_VIDEO_BYTES,
            i2v_router=make_i2v_router(),
            intro_bg_path=intro_bg,
            outro_path=outro,
            r2_public_base_url=R2_PUBLIC_BASE_URL,
        )

    # Layer B ran: stitch_clips was called exactly once
    stitch_clips_mock.assert_awaited_once()
    # persist was called: pipeline didn't crash
    persist_mock.assert_awaited_once()
    # The result is the T2V manifest (Layer B took over)
    manifest = persist_mock.call_args.kwargs["manifest"]
    assert manifest["manifest_kind"] == "video_mp4"
