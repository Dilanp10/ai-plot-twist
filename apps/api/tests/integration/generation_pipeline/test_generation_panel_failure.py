"""Partial panel failure: one provider exhausted → ready_degraded.

Module 008 / Task T-010.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domain.generation_pipeline import run_generation_pipeline
from app.providers.image.base import ImageProviderUnavailable, ImageRequest, ImageResult
from app.providers.image.fake import FakeImageProvider
from app.providers.image.router import ImageProviderRouter

from ._helpers import (
    CHAPTER_ID,
    PLACEHOLDER_URL,
    TTS_VOICE,
    make_ctx,
    make_mock_session,
    make_script,
    make_scriptwriter,
    make_uploader,
)

_MODULE = "app.domain.generation_pipeline"
_NEW_CHAPTER_DB_ID = 58


class _FailOnceImageProvider(FakeImageProvider):
    """Returns PNG_1x1 on first call, raises ImageProviderUnavailable on second."""

    _call_count: int = 0

    async def generate(self, req: ImageRequest) -> ImageResult:
        self._call_count += 1
        if self._call_count == 2:
            raise ImageProviderUnavailable("panel 2 failed")
        return await super().generate(req)


@pytest.mark.asyncio
async def test_partial_panel_failure_ready_degraded() -> None:
    """One panel failing → status='ready_degraded'."""
    script = make_script(n_clips=4)
    ctx = make_ctx()
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)

    failing_router = ImageProviderRouter(
        [_FailOnceImageProvider()],
        check_health=False,
        max_retries_on_unavailable=0,  # no retries so the single failure sticks
    )

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
            image_router=failing_router,
            uploader=make_uploader(),
            placeholder_url=PLACEHOLDER_URL,
            tts_voice=TTS_VOICE,
            panel_concurrency=1,  # serial so call_count is predictable
            deadline_s=60.0,
        )

    assert persist_mock.call_args.kwargs["status"] == "ready_degraded"


@pytest.mark.asyncio
async def test_partial_panel_failure_uses_placeholder_url() -> None:
    """The failed panel's image_url must be the placeholder URL."""
    script = make_script(n_clips=4)
    ctx = make_ctx()
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)

    failing_router = ImageProviderRouter(
        [_FailOnceImageProvider()],
        check_health=False,
        max_retries_on_unavailable=0,  # no retries so the single failure sticks
    )

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
            image_router=failing_router,
            uploader=make_uploader(),
            placeholder_url=PLACEHOLDER_URL,
            tts_voice=TTS_VOICE,
            panel_concurrency=1,
            deadline_s=60.0,
        )

    manifest = persist_mock.call_args.kwargs["manifest"]
    placeholder_count = sum(
        1 for p in manifest["panels"] if p["image_url"] == PLACEHOLDER_URL
    )
    assert placeholder_count >= 1


@pytest.mark.asyncio
async def test_all_panels_failed_still_ready_degraded() -> None:
    """All panels failing → status='ready_degraded' (not FAILED)."""
    script = make_script(n_clips=4)
    ctx = make_ctx()
    persist_mock = AsyncMock(return_value=_NEW_CHAPTER_DB_ID)

    # Empty router → always ImageProviderUnavailable
    empty_router = ImageProviderRouter([], check_health=False)

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
            image_router=empty_router,
            uploader=make_uploader(),
            placeholder_url=PLACEHOLDER_URL,
            tts_voice=TTS_VOICE,
            panel_concurrency=4,
            deadline_s=60.0,
        )

    assert persist_mock.call_args.kwargs["status"] == "ready_degraded"
    manifest = persist_mock.call_args.kwargs["manifest"]
    assert all(p["image_url"] == PLACEHOLDER_URL for p in manifest["panels"])
