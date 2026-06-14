"""Scriptwriter failure: LLM error propagates out of the pipeline.

Module 008 / Task T-010.

When the scriptwriter raises (all LLM providers exhausted), the pipeline
must NOT persist a chapter, must NOT transition the cycle, and must
re-raise so the side_effect wrapper can drive the cycle to FAILED.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.generation_pipeline import run_generation_pipeline
from app.domain.scriptwriter import Scriptwriter

from ._helpers import (
    CHAPTER_ID,
    PLACEHOLDER_URL,
    TTS_VOICE,
    make_ctx,
    make_image_router,
    make_mock_session,
    make_uploader,
)

_MODULE = "app.domain.generation_pipeline"


def _make_failing_scriptwriter(exc: BaseException) -> Scriptwriter:
    sw = MagicMock(spec=Scriptwriter)
    sw.draft = AsyncMock(side_effect=exc)
    return sw


@pytest.mark.asyncio
async def test_scriptwriter_error_propagates() -> None:
    """LLM failure propagates out of run_generation_pipeline."""
    ctx = make_ctx()

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=AsyncMock()) as persist_mock,
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
        pytest.raises(RuntimeError, match="LLM unavailable"),
    ):
        await run_generation_pipeline(
            CHAPTER_ID,
            session=make_mock_session(),
            scriptwriter=_make_failing_scriptwriter(RuntimeError("LLM unavailable")),
            image_router=make_image_router(),
            uploader=make_uploader(),
            placeholder_url=PLACEHOLDER_URL,
            tts_voice=TTS_VOICE,
            panel_concurrency=4,
            deadline_s=60.0,
        )

    persist_mock.assert_not_called()


@pytest.mark.asyncio
async def test_scriptwriter_error_does_not_persist_chapter() -> None:
    """No DB write must occur when the scriptwriter raises."""
    ctx = make_ctx()
    persist_mock = AsyncMock()
    transition_mock = AsyncMock()

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=transition_mock),
        pytest.raises(OSError, match="network error"),
    ):
        await run_generation_pipeline(
            CHAPTER_ID,
            session=make_mock_session(),
            scriptwriter=_make_failing_scriptwriter(OSError("network error")),
            image_router=make_image_router(),
            uploader=make_uploader(),
            placeholder_url=PLACEHOLDER_URL,
            tts_voice=TTS_VOICE,
            panel_concurrency=4,
            deadline_s=60.0,
        )

    persist_mock.assert_not_called()
    transition_mock.assert_not_called()


@pytest.mark.asyncio
async def test_scriptwriter_error_does_not_transition_cycle() -> None:
    """Cycle must NOT be transitioned when the scriptwriter raises."""
    ctx = make_ctx()
    transition_mock = AsyncMock()

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=AsyncMock()),
        patch(f"{_MODULE}._transition_to_pending_release", new=transition_mock),
        pytest.raises(ValueError),
    ):
        await run_generation_pipeline(
            CHAPTER_ID,
            session=make_mock_session(),
            scriptwriter=_make_failing_scriptwriter(ValueError("bad schema")),
            image_router=make_image_router(),
            uploader=make_uploader(),
            placeholder_url=PLACEHOLDER_URL,
            tts_voice=TTS_VOICE,
            panel_concurrency=4,
            deadline_s=60.0,
        )

    transition_mock.assert_not_called()
