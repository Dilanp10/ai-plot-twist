"""R2 upload failure: image upload error → placeholder + ready_degraded.

Module 008 / Task T-010.

R2UploadError is caught inside render_panel (never propagated), so the
chapter should still be persisted — just with placeholder image URLs and
status='ready_degraded'.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.generation_pipeline import run_generation_pipeline
from app.infra.r2_uploader import R2Uploader, R2UploadError

from ._helpers import (
    CHAPTER_ID,
    PLACEHOLDER_URL,
    TTS_VOICE,
    make_ctx,
    make_image_router,
    make_mock_session,
    make_script,
    make_scriptwriter,
)

_MODULE = "app.domain.generation_pipeline"
_NEW_CHAPTER_DB_ID = 61


def _make_failing_uploader() -> R2Uploader:
    """Return an uploader that always raises R2UploadError."""
    uploader = MagicMock(spec=R2Uploader)
    uploader.upload = AsyncMock(side_effect=R2UploadError("bucket unreachable"))
    return uploader


@pytest.mark.asyncio
async def test_r2_failure_ready_degraded() -> None:
    """R2 upload failure → status='ready_degraded'."""
    script = make_script(n_panels=3)
    ctx = make_ctx()
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
        patch("app.domain.panel_pipeline.synthesize", new=AsyncMock(return_value=None)),
    ):
        await run_generation_pipeline(
            CHAPTER_ID,
            session=make_mock_session(),
            scriptwriter=make_scriptwriter(script),
            image_router=make_image_router(),
            uploader=_make_failing_uploader(),
            placeholder_url=PLACEHOLDER_URL,
            tts_voice=TTS_VOICE,
            panel_concurrency=4,
            deadline_s=60.0,
        )

    assert persist_mock.call_args.kwargs["status"] == "ready_degraded"


@pytest.mark.asyncio
async def test_r2_failure_uses_placeholder_url() -> None:
    """All panels with failed R2 uploads must use placeholder_url."""
    script = make_script(n_panels=3)
    ctx = make_ctx()
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
        patch("app.domain.panel_pipeline.synthesize", new=AsyncMock(return_value=None)),
    ):
        await run_generation_pipeline(
            CHAPTER_ID,
            session=make_mock_session(),
            scriptwriter=make_scriptwriter(script),
            image_router=make_image_router(),
            uploader=_make_failing_uploader(),
            placeholder_url=PLACEHOLDER_URL,
            tts_voice=TTS_VOICE,
            panel_concurrency=4,
            deadline_s=60.0,
        )

    manifest = persist_mock.call_args.kwargs["manifest"]
    assert all(p["image_url"] == PLACEHOLDER_URL for p in manifest["panels"])


@pytest.mark.asyncio
async def test_r2_failure_chapter_is_persisted() -> None:
    """R2 upload failure must not prevent chapter persistence."""
    script = make_script(n_panels=3)
    ctx = make_ctx()
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)
    transition_mock = AsyncMock()

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=transition_mock),
        patch("app.domain.panel_pipeline.synthesize", new=AsyncMock(return_value=None)),
    ):
        await run_generation_pipeline(
            CHAPTER_ID,
            session=make_mock_session(),
            scriptwriter=make_scriptwriter(script),
            image_router=make_image_router(),
            uploader=_make_failing_uploader(),
            placeholder_url=PLACEHOLDER_URL,
            tts_voice=TTS_VOICE,
            panel_concurrency=4,
            deadline_s=60.0,
        )

    persist_mock.assert_called_once()
    transition_mock.assert_called_once()


@pytest.mark.asyncio
async def test_r2_failure_does_not_propagate() -> None:
    """R2UploadError must not escape run_generation_pipeline."""
    script = make_script(n_panels=3)
    ctx = make_ctx()

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=AsyncMock(return_value=_NEW_CHAPTER_DB_ID)),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
        patch("app.domain.panel_pipeline.synthesize", new=AsyncMock(return_value=None)),
    ):
        # Should complete without raising
        await run_generation_pipeline(
            CHAPTER_ID,
            session=make_mock_session(),
            scriptwriter=make_scriptwriter(script),
            image_router=make_image_router(),
            uploader=_make_failing_uploader(),
            placeholder_url=PLACEHOLDER_URL,
            tts_voice=TTS_VOICE,
            panel_concurrency=4,
            deadline_s=60.0,
        )
