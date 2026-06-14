"""Happy path: winner mode, all panels succeed, status='ready'.

Module 008 / Task T-010.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.generation_pipeline import run_generation_pipeline

from ._helpers import (
    CHAPTER_ID,
    CYCLE_ID,
    NEW_CHAPTER_PUBLIC_ID,
    PLACEHOLDER_URL,
    SEASON_ID,
    TTS_VOICE,
    make_ctx,
    make_image_router,
    make_mock_session,
    make_script,
    make_scriptwriter,
    make_uploader,
)

_MODULE = "app.domain.generation_pipeline"
_NEW_CHAPTER_DB_ID = 55


@pytest.mark.asyncio
async def test_happy_path_creates_ready_chapter() -> None:
    """Happy path: all panels succeed → status='ready'."""
    script = make_script(n_panels=3)
    ctx = make_ctx()
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

    persist_mock.assert_called_once()
    kwargs = persist_mock.call_args.kwargs
    assert kwargs["status"] == "ready"
    assert kwargs["cycle_id"] == CYCLE_ID
    assert kwargs["season_id"] == SEASON_ID
    assert kwargs["next_day_index"] == 11


@pytest.mark.asyncio
async def test_happy_path_manifest_has_correct_panel_count() -> None:
    script = make_script(n_panels=3)
    ctx = make_ctx()
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
    assert len(manifest["panels"]) == 3


@pytest.mark.asyncio
async def test_happy_path_transitions_cycle() -> None:
    script = make_script()
    ctx = make_ctx()
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
    assert transition_mock.call_args.args[1] == CYCLE_ID
    assert transition_mock.call_args.args[2] == _NEW_CHAPTER_DB_ID


@pytest.mark.asyncio
async def test_happy_path_panel_urls_not_placeholder() -> None:
    """All panels should have real (non-placeholder) image URLs."""
    script = make_script(n_panels=3)
    ctx = make_ctx()
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
    for panel in manifest["panels"]:
        assert panel["image_url"] != PLACEHOLDER_URL


@pytest.mark.asyncio
async def test_happy_path_new_chapter_public_id_used() -> None:
    """The pre-generated public UUID should appear in R2 keys."""
    script = make_script(n_panels=3)
    ctx = make_ctx()
    uploaded_keys: list[str] = []

    async def _capture_upload(key: str, body: bytes, content_type: str) -> str:
        uploaded_keys.append(key)
        return f"https://r2.example.com/{key}"

    from app.infra.r2_uploader import R2Uploader as _R2Uploader
    uploader = MagicMock(spec=_R2Uploader)
    uploader.upload = _capture_upload

    with (
        patch(f"{_MODULE}._load_ctx_from_db", new=AsyncMock(return_value=ctx)),
        patch(f"{_MODULE}._persist_new_chapter", new=AsyncMock(return_value=_NEW_CHAPTER_DB_ID)),
        patch(f"{_MODULE}._transition_to_pending_release", new=AsyncMock()),
    ):
        await run_generation_pipeline(
            CHAPTER_ID,
            session=make_mock_session(),
            scriptwriter=make_scriptwriter(script),
            image_router=make_image_router(),
            uploader=uploader,
            placeholder_url=PLACEHOLDER_URL,
            tts_voice=TTS_VOICE,
            panel_concurrency=4,
            deadline_s=60.0,
        )

    assert any(str(NEW_CHAPTER_PUBLIC_ID) in k for k in uploaded_keys)
