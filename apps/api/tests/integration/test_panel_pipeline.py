"""Integration tests: panel_pipeline.render_panel — fake image + mock uploader.

Module 008 / Task T-009.

No real network or R2 calls. Tests use:
  - FakeImageProvider inside a real ImageProviderRouter (check_health=False).
  - AsyncMock for R2Uploader.upload.
  - patch for app.domain.panel_pipeline.synthesize.

Coverage:
  - Success path: returns PanelResult with ok=True and correct fields.
  - ImageProviderUnavailable → placeholder PanelResult (ok=False).
  - R2UploadError on image upload → placeholder PanelResult (ok=False).
  - TTS succeeds → tts_url is a string.
  - synthesize returns None → tts_url is None, ok remains True.
  - R2UploadError on TTS upload → tts_url=None, ok remains True.
  - seed forwarded to image router equals stable_hash(chapter_id, panel.idx).
  - provider_used comes from image_result.provider.
  - Placeholder sets provider_used="placeholder".
  - R2 image key contains season_slug and str(chapter_public_id).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.domain.panel_pipeline import PanelResult, render_panel
from app.domain.scriptwriter_response_v1 import Panel
from app.domain.seed_derivation import stable_hash
from app.infra.r2_uploader import R2Uploader, R2UploadError
from app.providers.image.base import (
    ImageRequest,
    ImageResult,
)
from app.providers.image.fake import FakeImageProvider
from app.providers.image.router import ImageProviderRouter

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHAPTER_ID = 7
_CHAPTER_PUBLIC_ID: UUID = uuid4()
_SEASON_SLUG = "s01-el-tunel"
_PLACEHOLDER = "https://assets.example.com/static/placeholder.webp"
_TTS_VOICE = "es-AR-ElenaNeural"

_GOOD_VISUAL = "cinematic shot of a shattered mirror reflecting two worlds, 35mm"
_GOOD_NARRATION = "El espejo crujió como hielo viejo al amanecer."
_GOOD_TTS = "El espejo crujió como hielo viejo al amanecer."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _panel(idx: int = 1) -> Panel:
    return Panel(
        idx=idx,
        narration=_GOOD_NARRATION,
        visual_prompt=_GOOD_VISUAL,
        mood="tense",
        tts_text=_GOOD_TTS,
    )


def _fake_image_router() -> ImageProviderRouter:
    """Router backed by FakeImageProvider — returns 1x1 PNG, no I/O."""
    return ImageProviderRouter(
        [FakeImageProvider()],
        check_health=False,
        backoff_schedule_seconds=(0.0,),
    )


def _exhausted_router() -> ImageProviderRouter:
    """Router with no providers → always raises ImageProviderUnavailable."""
    return ImageProviderRouter([], check_health=False)


def _mock_uploader(url: str = "https://r2.example.com/panel-1.png") -> R2Uploader:
    """R2Uploader whose upload() always returns *url*."""
    uploader = MagicMock(spec=R2Uploader)
    uploader.upload = AsyncMock(return_value=url)
    return uploader


async def _render(
    *,
    panel: Panel | None = None,
    image_router: ImageProviderRouter | None = None,
    uploader: R2Uploader | None = None,
    tts_bytes: bytes | None = b"mp3data",
) -> PanelResult:
    """Helper that calls render_panel with sensible defaults."""
    with patch(
        "app.domain.panel_pipeline.synthesize",
        new=AsyncMock(return_value=tts_bytes),
    ):
        return await render_panel(
            panel=panel or _panel(),
            chapter_id=_CHAPTER_ID,
            chapter_public_id=_CHAPTER_PUBLIC_ID,
            season_slug=_SEASON_SLUG,
            image_router=image_router or _fake_image_router(),
            uploader=uploader or _mock_uploader(),
            tts_voice=_TTS_VOICE,
            placeholder_url=_PLACEHOLDER,
        )


# ---------------------------------------------------------------------------
# Tests — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_panel_ok_returns_panel_result() -> None:
    result = await _render()
    assert isinstance(result, PanelResult)
    assert result.ok is True
    assert result.idx == 1


@pytest.mark.asyncio
async def test_render_panel_image_url_from_uploader() -> None:
    uploader = _mock_uploader("https://r2.example.com/image.png")
    # First call is image upload; second (if any) is TTS.
    result = await _render(uploader=uploader)
    assert result.image_url == "https://r2.example.com/image.png"


@pytest.mark.asyncio
async def test_render_panel_provider_used_from_image_result() -> None:
    result = await _render()
    # FakeImageProvider.name == "fake"
    assert result.provider_used == "fake"


@pytest.mark.asyncio
async def test_render_panel_idx_preserved() -> None:
    result = await _render(panel=_panel(idx=3))
    assert result.idx == 3


@pytest.mark.asyncio
async def test_render_panel_blurhash_is_none() -> None:
    result = await _render()
    assert result.image_blurhash is None


# ---------------------------------------------------------------------------
# Tests — seed derivation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_panel_seed_is_stable_hash() -> None:
    """The seed forwarded to the router must equal stable_hash(chapter_id, panel.idx)."""
    captured: list[ImageRequest] = []

    async def _capturing_render(req: ImageRequest) -> ImageResult:
        captured.append(req)
        return ImageResult(
            bytes_=b"x",
            mime_type="image/png",
            provider="capturing",
            model="m",
            latency_ms=0,
        )

    router = MagicMock(spec=ImageProviderRouter)
    router.render = _capturing_render
    uploader = _mock_uploader()

    panel = _panel(idx=2)
    await _render(panel=panel, image_router=router, uploader=uploader)

    assert len(captured) == 1
    assert captured[0].seed == stable_hash(_CHAPTER_ID, 2)


# ---------------------------------------------------------------------------
# Tests — placeholder on image failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_panel_provider_unavailable_returns_placeholder() -> None:
    result = await _render(image_router=_exhausted_router())
    assert result.ok is False
    assert result.image_url == _PLACEHOLDER
    assert result.provider_used == "placeholder"
    assert result.tts_url is None


@pytest.mark.asyncio
async def test_render_panel_r2_image_upload_error_returns_placeholder() -> None:
    uploader = MagicMock(spec=R2Uploader)
    uploader.upload = AsyncMock(side_effect=R2UploadError("exhausted"))

    result = await _render(uploader=uploader)

    assert result.ok is False
    assert result.image_url == _PLACEHOLDER
    assert result.provider_used == "placeholder"
    assert result.tts_url is None


# ---------------------------------------------------------------------------
# Tests — TTS path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_panel_tts_success_populates_tts_url() -> None:
    image_url = "https://r2.example.com/image.png"
    tts_url = "https://r2.example.com/tts.mp3"
    uploader = MagicMock(spec=R2Uploader)
    uploader.upload = AsyncMock(side_effect=[image_url, tts_url])

    result = await _render(uploader=uploader, tts_bytes=b"mp3data")

    assert result.tts_url == tts_url
    assert result.ok is True


@pytest.mark.asyncio
async def test_render_panel_tts_none_does_not_degrade() -> None:
    result = await _render(tts_bytes=None)
    assert result.tts_url is None
    assert result.ok is True


@pytest.mark.asyncio
async def test_render_panel_tts_upload_error_does_not_degrade() -> None:
    image_url = "https://r2.example.com/image.png"
    uploader = MagicMock(spec=R2Uploader)
    # Image upload succeeds; TTS upload fails.
    uploader.upload = AsyncMock(
        side_effect=[image_url, R2UploadError("tts upload failed")]
    )

    result = await _render(uploader=uploader, tts_bytes=b"mp3data")

    assert result.ok is True
    assert result.tts_url is None


# ---------------------------------------------------------------------------
# Tests — R2 key shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_panel_r2_key_contains_season_slug() -> None:
    uploader = MagicMock(spec=R2Uploader)
    uploader.upload = AsyncMock(return_value="https://r2.example.com/x")
    captured_keys: list[str] = []

    async def _capture(key: str, body: bytes, content_type: str) -> str:
        captured_keys.append(key)
        return f"https://r2.example.com/{key}"

    uploader.upload = _capture

    await _render(uploader=uploader)

    assert any(_SEASON_SLUG in k for k in captured_keys)


@pytest.mark.asyncio
async def test_render_panel_r2_key_contains_chapter_public_id() -> None:
    captured_keys: list[str] = []

    async def _capture(key: str, body: bytes, content_type: str) -> str:
        captured_keys.append(key)
        return f"https://r2.example.com/{key}"

    uploader = MagicMock(spec=R2Uploader)
    uploader.upload = _capture

    await _render(uploader=uploader)

    assert any(str(_CHAPTER_PUBLIC_ID) in k for k in captured_keys)


@pytest.mark.asyncio
async def test_render_panel_tts_key_contains_tts_suffix() -> None:
    captured_keys: list[str] = []

    async def _capture(key: str, body: bytes, content_type: str) -> str:
        captured_keys.append(key)
        return f"https://r2.example.com/{key}"

    uploader = MagicMock(spec=R2Uploader)
    uploader.upload = _capture

    await _render(uploader=uploader, tts_bytes=b"mp3bytes")

    assert any("-tts-" in k for k in captured_keys)
    assert any(k.endswith(".mp3") for k in captured_keys)
