"""T2V failure (all clips fail) → coordinator falls back to T2I path.

Module 008 / Task T-010 delta.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domain.generation_pipeline import run_generation_pipeline

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
_NEW_CHAPTER_DB_ID = 71


@pytest.mark.asyncio
async def test_all_clips_fail_falls_back_to_comic() -> None:
    """Empty video router → AllClipsFailedError → T2I path → comic_panels."""
    script = make_script(n_clips=4)
    ctx = make_ctx()
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
        patch("app.domain.clip_pipeline.synthesize", new=AsyncMock(return_value=None)),
        patch("app.domain.panel_pipeline.synthesize", new=AsyncMock(return_value=None)),
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
            video_router=make_video_router(fail=True),  # empty chain → all clips fail
            placeholder_video_url=PLACEHOLDER_VIDEO_URL,
            placeholder_video_bytes=PLACEHOLDER_VIDEO_BYTES,
            video_pipeline_enabled=True,
        )

    manifest = persist_mock.call_args.kwargs["manifest"]
    assert manifest["schema_version"] == "1.0"
    assert manifest["manifest_kind"] == "comic_panels"
    assert "panels" in manifest


@pytest.mark.asyncio
async def test_all_clips_fail_degraded_reason_in_manifest() -> None:
    script = make_script(n_clips=4)
    ctx = make_ctx()
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
        patch("app.domain.clip_pipeline.synthesize", new=AsyncMock(return_value=None)),
        patch("app.domain.panel_pipeline.synthesize", new=AsyncMock(return_value=None)),
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
            video_router=make_video_router(fail=True),
            placeholder_video_url=PLACEHOLDER_VIDEO_URL,
            placeholder_video_bytes=PLACEHOLDER_VIDEO_BYTES,
            video_pipeline_enabled=True,
        )

    manifest = persist_mock.call_args.kwargs["manifest"]
    gm = manifest["generation_metadata"]
    assert "all_clips_failed" in gm["degraded_reasons"]
    assert gm["degraded"] is True


@pytest.mark.asyncio
async def test_all_clips_fail_status_ready_degraded() -> None:
    script = make_script(n_clips=4)
    ctx = make_ctx()
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
        patch("app.domain.clip_pipeline.synthesize", new=AsyncMock(return_value=None)),
        patch("app.domain.panel_pipeline.synthesize", new=AsyncMock(return_value=None)),
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
            video_router=make_video_router(fail=True),
            placeholder_video_url=PLACEHOLDER_VIDEO_URL,
            placeholder_video_bytes=PLACEHOLDER_VIDEO_BYTES,
        )

    # Fallback always marks ready_degraded (the synthetic reason is present).
    assert persist_mock.call_args.kwargs["status"] == "ready_degraded"


@pytest.mark.asyncio
async def test_all_clips_fail_t2i_panels_use_real_images() -> None:
    """Image router is intact → fallback panels render with real (non-placeholder) URLs."""
    script = make_script(n_clips=4)
    ctx = make_ctx()
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
        patch("app.domain.clip_pipeline.synthesize", new=AsyncMock(return_value=None)),
        patch("app.domain.panel_pipeline.synthesize", new=AsyncMock(return_value=None)),
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
            video_router=make_video_router(fail=True),
            placeholder_video_url=PLACEHOLDER_VIDEO_URL,
            placeholder_video_bytes=PLACEHOLDER_VIDEO_BYTES,
        )

    manifest = persist_mock.call_args.kwargs["manifest"]
    for p in manifest["panels"]:
        assert p["image_url"] != PLACEHOLDER_URL
