"""Deadline exceeded: panels that don't finish in time → ready_degraded.

Module 008 / Task T-010.

Uses a patched render_panel that sleeps indefinitely combined with a
1 ms deadline to exercise the asyncio.wait_for + tracker path in
_run_panels without adding real wall-clock delay.
"""

from __future__ import annotations

import asyncio
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
)

_MODULE = "app.domain.generation_pipeline"
_PANEL_PIPELINE_MODULE = "app.domain.panel_pipeline"
_NEW_CHAPTER_DB_ID = 60


async def _forever_render(**kwargs: object) -> object:
    """Never returns — simulates a hung image provider."""
    await asyncio.sleep(3600)
    raise AssertionError("should not reach here")


@pytest.mark.asyncio
async def test_deadline_produces_ready_degraded() -> None:
    """Deadline exceeded → status='ready_degraded'."""
    script = make_script(n_panels=3)
    ctx = make_ctx()
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
        patch(f"{_MODULE}.render_panel", new=AsyncMock(side_effect=_forever_render)),
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
            deadline_s=0.001,  # 1 ms — fires before any panel can start
        )

    assert persist_mock.call_args.kwargs["status"] == "ready_degraded"


@pytest.mark.asyncio
async def test_deadline_all_panels_get_placeholder() -> None:
    """All panels that miss the deadline use placeholder_url."""
    script = make_script(n_panels=3)
    ctx = make_ctx()
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
        patch(f"{_MODULE}.render_panel", new=AsyncMock(side_effect=_forever_render)),
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
            deadline_s=0.001,
        )

    manifest = persist_mock.call_args.kwargs["manifest"]
    assert all(p["image_url"] == PLACEHOLDER_URL for p in manifest["panels"])


@pytest.mark.asyncio
async def test_deadline_chapter_is_still_persisted() -> None:
    """Even on deadline, chapter must be persisted (degraded, not lost)."""
    script = make_script(n_panels=3)
    ctx = make_ctx()
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)
    transition_mock = AsyncMock()

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=transition_mock),
        patch(f"{_MODULE}.render_panel", new=AsyncMock(side_effect=_forever_render)),
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
            deadline_s=0.001,
        )

    persist_mock.assert_called_once()
    transition_mock.assert_called_once()


@pytest.mark.asyncio
async def test_deadline_manifest_has_correct_panel_count() -> None:
    """Manifest must still contain all expected panels even after timeout."""
    script = make_script(n_panels=4)
    ctx = make_ctx(n_panels=4)
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=persist_mock),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
        patch(f"{_MODULE}.render_panel", new=AsyncMock(side_effect=_forever_render)),
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
            deadline_s=0.001,
        )

    manifest = persist_mock.call_args.kwargs["manifest"]
    assert len(manifest["panels"]) == 4
