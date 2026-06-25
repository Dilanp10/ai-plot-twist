"""Layer A (I2V) happy path: coordinator selects Layer A, produces video_i2v manifest.

Delta 008.

All ffmpeg, I2V provider, and DB calls are mocked so the suite runs without
external dependencies.
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
    R2_PUBLIC_BASE_URL,
    TTS_VOICE,
    make_ctx,
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
_NEW_CHAPTER_DB_ID = 80

_T2V_STITCH = StitchResult(
    video_url="https://r2.example.com/chapter-t2v.mp4",
    video_duration_s=20.0,
    video_bytes_len=1024,
)


def _make_assets(tmp_path: Path) -> tuple[Path, Path]:
    intro_bg = tmp_path / "intro_bg.png"
    intro_bg.write_bytes(b"\x89PNG")
    outro = tmp_path / "outro.mp4"
    outro.write_bytes(b"FAKEOUTRO")
    return intro_bg, outro


@pytest.mark.asyncio
async def test_i2v_happy_manifest_kind_is_video_i2v(tmp_path: Path) -> None:
    ctx = make_ctx_i2v()
    script_v3 = make_script_v3()
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)
    intro_bg, outro = _make_assets(tmp_path)

    i2v_body = make_stub_i2v_body_result(tmp_path)
    stitch_a = make_stub_stitch_layer_a_result()

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
        patch(f"{_MODULE}.run_i2v", new=AsyncMock(return_value=i2v_body)),
        patch(f"{_MODULE}.render_intro", new=AsyncMock()),
        patch(f"{_MODULE}.stitch_layer_a", new=AsyncMock(return_value=stitch_a)),
        patch(
            "app.domain.scriptwriter.Scriptwriter.draft_v3",
            new=AsyncMock(return_value=script_v3),
        ),
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
            i2v_router=make_i2v_router(),
            intro_bg_path=intro_bg,
            outro_path=outro,
            r2_public_base_url=R2_PUBLIC_BASE_URL,
        )

    manifest = persist_mock.call_args.kwargs["manifest"]
    assert manifest["schema_version"] == "3.0"
    assert manifest["manifest_kind"] == "video_i2v"


@pytest.mark.asyncio
async def test_i2v_happy_manifest_has_video_url(tmp_path: Path) -> None:
    ctx = make_ctx_i2v()
    script_v3 = make_script_v3()
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)
    intro_bg, outro = _make_assets(tmp_path)

    i2v_body = make_stub_i2v_body_result(tmp_path)
    stub_stitch = make_stub_stitch_layer_a_result()

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
        patch(f"{_MODULE}.run_i2v", new=AsyncMock(return_value=i2v_body)),
        patch(f"{_MODULE}.render_intro", new=AsyncMock()),
        patch(f"{_MODULE}.stitch_layer_a", new=AsyncMock(return_value=stub_stitch)),
        patch(
            "app.domain.scriptwriter.Scriptwriter.draft_v3",
            new=AsyncMock(return_value=script_v3),
        ),
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
            i2v_router=make_i2v_router(),
            intro_bg_path=intro_bg,
            outro_path=outro,
            r2_public_base_url=R2_PUBLIC_BASE_URL,
        )

    manifest = persist_mock.call_args.kwargs["manifest"]
    assert manifest["video_url"] == stub_stitch.video_url
    assert manifest["video_duration_s"] == 14.0


@pytest.mark.asyncio
async def test_i2v_happy_status_is_ready(tmp_path: Path) -> None:
    ctx = make_ctx_i2v()
    script_v3 = make_script_v3()
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)
    intro_bg, outro = _make_assets(tmp_path)

    i2v_body = make_stub_i2v_body_result(tmp_path)
    stitch_a = make_stub_stitch_layer_a_result()

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
        patch(f"{_MODULE}.run_i2v", new=AsyncMock(return_value=i2v_body)),
        patch(f"{_MODULE}.render_intro", new=AsyncMock()),
        patch(f"{_MODULE}.stitch_layer_a", new=AsyncMock(return_value=stitch_a)),
        patch(
            "app.domain.scriptwriter.Scriptwriter.draft_v3",
            new=AsyncMock(return_value=script_v3),
        ),
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
            i2v_router=make_i2v_router(),
            intro_bg_path=intro_bg,
            outro_path=outro,
            r2_public_base_url=R2_PUBLIC_BASE_URL,
        )

    assert persist_mock.call_args.kwargs["status"] == "ready"


@pytest.mark.asyncio
async def test_i2v_happy_no_character_disables_layer_a(tmp_path: Path) -> None:
    """Without winner_character_r2_key, coordinator skips Layer A → Layer B (T2V)."""
    ctx = make_ctx()  # no winner_character_r2_key
    script = make_script(n_clips=4)
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)
    intro_bg, outro = _make_assets(tmp_path)

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
        patch(f"{_MODULE}.stitch_clips", new=AsyncMock(return_value=_T2V_STITCH)),
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
            i2v_router=make_i2v_router(),
            intro_bg_path=intro_bg,
            outro_path=outro,
            r2_public_base_url=R2_PUBLIC_BASE_URL,
        )

    manifest = persist_mock.call_args.kwargs["manifest"]
    assert manifest["manifest_kind"] == "video_mp4"
