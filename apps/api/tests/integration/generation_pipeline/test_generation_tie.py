"""Tiebreak scenario: winner with tiebreak flag set.

Module 008 / Task T-010.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.domain.generation_pipeline import run_generation_pipeline
from app.domain.winner_selector import WinnerPick

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
)

_MODULE = "app.domain.generation_pipeline"
_NEW_CHAPTER_DB_ID = 56


def _tiebreak_pick() -> WinnerPick:
    return WinnerPick(
        winner_twist_id=88,
        winner_public_id=uuid4(),
        winner_user_display_name="Bob",
        vote_count=5,
        tiebreak=True,
        runner_up_twist_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_tie_chapter_is_ready() -> None:
    """Tiebreak should still produce status='ready' when all panels succeed."""
    script = make_script()
    ctx = make_ctx(winner_pick=_tiebreak_pick(), winner_content="Bob's twist content.")
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
async def test_tie_manifest_has_tiebreak_flag() -> None:
    """winner_metadata.tiebreak should be True in the manifest."""
    script = make_script()
    pick = _tiebreak_pick()
    ctx = make_ctx(winner_pick=pick, winner_content="Giro del empate.")
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
    assert wm["tiebreak"] is True
    assert wm["runner_up_twist_id"] == str(pick.runner_up_twist_id)


@pytest.mark.asyncio
async def test_tie_winner_author_in_manifest() -> None:
    script = make_script()
    pick = _tiebreak_pick()
    ctx = make_ctx(winner_pick=pick, winner_content="Giro del empate.")
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
    assert manifest["winner_metadata"]["winner_author_display_name"] == "Bob"
