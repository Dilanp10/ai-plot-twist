"""Auto-continue mode: no approved winner twist.

Module 008 / Task T-010.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domain.generation_pipeline import run_generation_pipeline

from ._helpers import (
    CHAPTER_ID,
    PLACEHOLDER_URL,
    TTS_VOICE,
    make_ctx,
    make_image_router,
    make_mock_session,
    make_script,
    make_scriptwriter,
    make_uploader,
    make_winner_pick,
)

_MODULE = "app.domain.generation_pipeline"
_NEW_CHAPTER_DB_ID = 57


@pytest.mark.asyncio
async def test_no_winner_chapter_is_ready() -> None:
    """Auto-continue mode should produce status='ready' when all panels succeed."""
    script = make_script()
    # No winner: winner_content=None
    ctx = make_ctx(winner_pick=make_winner_pick(with_winner=False), winner_content=None)
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
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
        )

    assert persist_mock.call_args.kwargs["status"] == "ready"


@pytest.mark.asyncio
async def test_no_winner_manifest_winner_metadata_all_null() -> None:
    """winner_metadata should be all-null in auto-continue mode."""
    script = make_script()
    ctx = make_ctx(winner_pick=make_winner_pick(with_winner=False), winner_content=None)
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
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
        )

    manifest = persist_mock.call_args.kwargs["manifest"]
    wm = manifest["winner_metadata"]
    assert wm["winner_twist_id"] is None
    assert wm["winner_author_display_name"] is None
    assert wm["vote_count"] == 0


@pytest.mark.asyncio
async def test_no_winner_cycle_still_transitions() -> None:
    """Auto-continue mode must still transition the cycle."""
    script = make_script()
    ctx = make_ctx(winner_pick=make_winner_pick(with_winner=False), winner_content=None)
    transition_mock = AsyncMock()

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=AsyncMock(return_value=_NEW_CHAPTER_DB_ID)),
        patch(f"{_MODULE}._transition_to_pending_release", new=transition_mock),
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
        )

    transition_mock.assert_called_once()
