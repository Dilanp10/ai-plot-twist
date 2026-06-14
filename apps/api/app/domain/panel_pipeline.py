"""Panel pipeline — single-panel render, TTS, and R2 upload.

Module 008 / Task T-009.

Handles one panel end-to-end:
  1. Derive a deterministic seed from ``(chapter_id, panel.idx)``.
  2. Call :class:`ImageProviderRouter` to render the image.
  3. Upload the image bytes to R2 via :class:`R2Uploader`.
  4. Synthesize TTS (best-effort) and upload the MP3.
  5. Return a :class:`PanelResult`.

Failure semantics:

- **Image provider exhausted** (``ImageProviderUnavailable``) → return a
  placeholder ``PanelResult`` with ``ok=False``.
- **R2 image upload exhausted** (``R2UploadError``) → return placeholder.
- **TTS failure / upload failure** → log and return ``tts_url=None``; does NOT
  degrade the panel (``ok`` remains ``True``).
- **``ImageProviderError``** (auth, operator config) → re-raise; the
  coordinator catches it via ``asyncio.gather(return_exceptions=True)`` and
  substitutes a placeholder at its level.
- **``asyncio.CancelledError``** → propagates freely; the deadline coordinator
  cancels outstanding tasks when the hard deadline is exceeded.

``image_blurhash`` is currently always ``None``; blurhash computation will be
added once Pillow is a declared dependency (tracked separately).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from uuid import UUID

from app.domain.scriptwriter_response import Panel
from app.domain.seed_derivation import stable_hash
from app.domain.tts_synthesizer import synthesize
from app.infra.r2_uploader import R2Uploader, R2UploadError
from app.providers.image.base import ImageProviderUnavailable
from app.providers.image.paths import compute_r2_path
from app.providers.image.router import ImageProviderRouter

logger = logging.getLogger(__name__)


@dataclass
class PanelResult:
    """Outcome of rendering one panel.

    Attributes
    ----------
    idx:
        1-based panel index.
    image_url:
        R2 public URL of the uploaded image, or *placeholder_url* on failure.
    image_blurhash:
        BlurHash string for progressive loading; currently always ``None``.
    tts_url:
        R2 public URL of the uploaded MP3, or ``None`` if TTS was skipped or
        failed.
    provider_used:
        Lower-case provider identifier (``"pollinations"``, ``"hf"``,
        ``"placeholder"``).
    ok:
        ``False`` when this panel uses the placeholder image; ``True``
        otherwise.
    """

    idx: int
    image_url: str
    image_blurhash: str | None
    tts_url: str | None
    provider_used: str
    ok: bool


async def render_panel(
    *,
    panel: Panel,
    chapter_id: int,
    chapter_public_id: UUID,
    season_slug: str,
    image_router: ImageProviderRouter,
    uploader: R2Uploader,
    tts_voice: str,
    placeholder_url: str,
) -> PanelResult:
    """Render *panel* end-to-end and return a :class:`PanelResult`.

    Parameters
    ----------
    panel:
        The scriptwriter panel containing ``visual_prompt`` and ``tts_text``.
    chapter_id:
        Internal integer chapter id (used for seed derivation only).
    chapter_public_id:
        Chapter's public UUID (used in R2 path construction).
    season_slug:
        URL-safe season identifier (e.g. ``"s01-el-tunel"``).
    image_router:
        Pre-configured image provider router.
    uploader:
        Pre-configured R2 uploader.
    tts_voice:
        edge-tts voice name (e.g. ``"es-AR-ElenaNeural"``).
    placeholder_url:
        Full public URL of the static placeholder image, returned as
        ``image_url`` when rendering fails.
    """
    seed = stable_hash(chapter_id, panel.idx)

    logger.info(
        "panel_render_started idx=%d seed=%d",
        panel.idx,
        seed,
    )

    # -------------------------------------------------------------------------
    # Image render
    # -------------------------------------------------------------------------
    from app.providers.image.base import ImageRequest  # local import avoids cycle

    req = ImageRequest(prompt=panel.visual_prompt, seed=seed)

    try:
        image_result = await image_router.render(req)
    except ImageProviderUnavailable:
        logger.warning(
            "panel_render_failed idx=%d reason=all_providers_exhausted",
            panel.idx,
        )
        return PanelResult(
            idx=panel.idx,
            image_url=placeholder_url,
            image_blurhash=None,
            tts_url=None,
            provider_used="placeholder",
            ok=False,
        )

    # -------------------------------------------------------------------------
    # Upload image to R2
    # -------------------------------------------------------------------------
    image_key = compute_r2_path(
        season_slug,
        str(chapter_public_id),
        panel.idx,
        image_result,
    )

    try:
        image_url = await uploader.upload(
            image_key,
            image_result.bytes_,
            image_result.mime_type,
        )
    except R2UploadError:
        logger.warning(
            "panel_r2_upload_failed idx=%d key=%s reason=r2_exhausted",
            panel.idx,
            image_key,
        )
        return PanelResult(
            idx=panel.idx,
            image_url=placeholder_url,
            image_blurhash=None,
            tts_url=None,
            provider_used="placeholder",
            ok=False,
        )

    logger.info(
        "panel_render_done idx=%d provider=%s model=%s latency_ms=%d ok=True",
        panel.idx,
        image_result.provider,
        image_result.model,
        image_result.latency_ms,
    )

    # -------------------------------------------------------------------------
    # TTS — best-effort; failures do NOT affect ok / degrade the chapter
    # -------------------------------------------------------------------------
    tts_url: str | None = None

    tts_bytes = await synthesize(panel.tts_text, voice=tts_voice)
    if tts_bytes is not None:
        tts_sha = hashlib.sha256(tts_bytes).hexdigest()[:8]
        tts_key = (
            f"seasons/{season_slug}/{chapter_public_id}/"
            f"{panel.idx}-tts-{tts_sha}.mp3"
        )
        try:
            tts_url = await uploader.upload(tts_key, tts_bytes, "audio/mpeg")
            logger.info("tts_done idx=%d ok=True", panel.idx)
        except R2UploadError:
            logger.warning(
                "tts_upload_failed idx=%d reason=r2_exhausted",
                panel.idx,
            )

    return PanelResult(
        idx=panel.idx,
        image_url=image_url,
        image_blurhash=None,  # TODO: compute from image_result.bytes_ once Pillow is available
        tts_url=tts_url,
        provider_used=image_result.provider,
        ok=True,
    )
