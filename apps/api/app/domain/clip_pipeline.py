"""Clip pipeline — single-clip video render, TTS, and R2 upload.

Module 008 / Task T-009 delta.

Handles one clip end-to-end:
  1. Derive a deterministic seed from ``(chapter_id, clip.idx)``.
  2. Call :class:`VideoProviderRouter` to render the T2V clip.
  3. Write clip bytes to ``tmp_dir / f"clip_{idx}.mp4"`` (ffmpeg needs a file).
  4. Upload clip bytes to R2 via :func:`compute_r2_clip_path`.
  5. Synthesize TTS (best-effort) and write to ``tmp_dir / f"audio_{idx}.mp3"``.
  6. Return a :class:`ClipResult`.

Failure semantics:

- **Video provider exhausted** (``VideoProviderError``) → write placeholder bytes
  to tmp file; return ``ClipResult(ok=False, provider_used="placeholder")``.
- **R2 image upload error** (``R2UploadError``) → same as above.
- **TTS failure** → ``tts_path=None``; does NOT degrade the clip (``ok``
  remains ``True``). Same fire-and-forget semantics as the panel pipeline.
- **``asyncio.CancelledError``** → propagates freely; deadline logic uses this.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.domain.scriptwriter_response import Clip
from app.domain.scriptwriter_response_v3 import Scene
from app.domain.seed_derivation import stable_hash
from app.domain.tts_synthesizer import synthesize
from app.infra.r2_uploader import R2Uploader, R2UploadError
from app.providers.i2v.base import I2VProviderError, I2VRequest, I2VResult
from app.providers.i2v.router import ImageToVideoProviderRouter
from app.providers.video import (
    VideoProviderError,
    VideoProviderRouter,
    VideoRequest,
    compute_r2_clip_path,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class ClipResult:
    """Outcome of rendering one video clip.

    Attributes
    ----------
    idx:
        1-based clip index.
    clip_url:
        R2 public URL of the uploaded clip mp4, or *placeholder_video_url* on
        failure.
    clip_path:
        Absolute local path of the mp4 file written to ``tmp_dir`` (for ffmpeg
        stitch input). Always written — placeholder bytes on failure.
    tts_path:
        Absolute local path of the audio mp3 file written to ``tmp_dir``, or
        ``None`` if TTS was skipped or failed.
    duration_s:
        Clip duration in seconds as reported by the video provider (0.0 for
        placeholder clips).
    provider_used:
        Lower-case provider identifier (``"hf"``, ``"pollinations"``,
        ``"placeholder"``).
    ok:
        ``False`` when this clip uses placeholder video; ``True`` otherwise.
    """

    idx: int
    clip_url: str
    clip_path: str
    tts_path: str | None
    duration_s: float
    provider_used: str
    ok: bool


class AllClipsFailedError(Exception):
    """Raised by the coordinator when every ClipResult.ok is False."""


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def render_clip(
    *,
    clip: Clip,
    chapter_id: int,
    chapter_public_id: UUID,
    season_slug: str,
    video_router: VideoProviderRouter,
    uploader: R2Uploader,
    tts_voice: str,
    placeholder_video_url: str,
    placeholder_bytes: bytes,
    tmp_dir: Path,
    duration_s: float = 5.0,
) -> ClipResult:
    """Render *clip* end-to-end and return a :class:`ClipResult`.

    Parameters
    ----------
    clip:
        The scriptwriter clip containing ``visual_prompt`` and ``tts_text``.
    chapter_id:
        Internal integer chapter id (for seed derivation only).
    chapter_public_id:
        Chapter's public UUID (used in R2 path construction).
    season_slug:
        URL-safe season identifier (e.g. ``"s01-el-tunel"``).
    video_router:
        Pre-configured video provider router.
    uploader:
        Pre-configured R2 uploader.
    tts_voice:
        edge-tts voice name (e.g. ``"es-AR-ElenaNeural"``).
    placeholder_video_url:
        Public R2 URL of the static placeholder mp4 (written to manifest on
        failure).
    placeholder_bytes:
        Bytes of the placeholder mp4 (written to tmp file on failure so ffmpeg
        always has a valid input).
    tmp_dir:
        Temporary directory for this chapter's generation run. Must exist.
    duration_s:
        Requested clip duration in seconds (passed to VideoRequest).
    """
    seed = stable_hash(chapter_id, clip.idx)
    clip_tmp = tmp_dir / f"clip_{clip.idx}.mp4"

    logger.info(
        "clip_render_started clip_idx=%d seed=%d duration_s=%.1f",
        clip.idx,
        seed,
        duration_s,
    )

    # -------------------------------------------------------------------------
    # Video render
    # -------------------------------------------------------------------------
    req = VideoRequest(
        prompt=clip.visual_prompt,
        seed=seed,
        duration_s=duration_s,
        width=512,
        height=512,
        fps=24,
        aspect="9:16",
    )

    try:
        video_result = await video_router.generate(req)
    except VideoProviderError:
        logger.warning(
            "clip_render_failed clip_idx=%d reason=all_providers_exhausted",
            clip.idx,
        )
        clip_tmp.write_bytes(placeholder_bytes)
        return ClipResult(
            idx=clip.idx,
            clip_url=placeholder_video_url,
            clip_path=str(clip_tmp),
            tts_path=None,
            duration_s=0.0,
            provider_used="placeholder",
            ok=False,
        )

    # -------------------------------------------------------------------------
    # Write clip to tmp (ffmpeg concat needs a local file)
    # -------------------------------------------------------------------------
    clip_tmp.write_bytes(video_result.bytes_)

    # -------------------------------------------------------------------------
    # Upload clip to R2
    # -------------------------------------------------------------------------
    clip_key = compute_r2_clip_path(
        season_slug,
        str(chapter_public_id),
        clip.idx,
        video_result,
    )

    try:
        clip_url = await uploader.upload(
            clip_key,
            video_result.bytes_,
            "video/mp4",
        )
    except R2UploadError:
        logger.warning(
            "clip_r2_upload_failed clip_idx=%d key=%s reason=r2_exhausted",
            clip.idx,
            clip_key,
        )
        clip_tmp.write_bytes(placeholder_bytes)
        return ClipResult(
            idx=clip.idx,
            clip_url=placeholder_video_url,
            clip_path=str(clip_tmp),
            tts_path=None,
            duration_s=0.0,
            provider_used="placeholder",
            ok=False,
        )

    logger.info(
        "clip_render_done clip_idx=%d provider=%s model=%s latency_ms=%d ok=True",
        clip.idx,
        video_result.provider,
        video_result.model,
        video_result.latency_ms,
    )

    # -------------------------------------------------------------------------
    # TTS — best-effort; failure does NOT affect ok / degrade the chapter
    # -------------------------------------------------------------------------
    tts_path: str | None = None

    tts_bytes = await synthesize(clip.tts_text, voice=tts_voice)
    if tts_bytes is not None:
        audio_tmp = tmp_dir / f"audio_{clip.idx}.mp3"
        try:
            audio_tmp.write_bytes(tts_bytes)
            tts_path = str(audio_tmp)
            logger.info("tts_done clip_idx=%d ok=True", clip.idx)
        except OSError:
            logger.warning("tts_write_failed clip_idx=%d", clip.idx)
    else:
        logger.info("tts_done clip_idx=%d ok=False", clip.idx)

    return ClipResult(
        idx=clip.idx,
        clip_url=clip_url,
        clip_path=str(clip_tmp),
        tts_path=tts_path,
        duration_s=video_result.duration_s,
        provider_used=video_result.provider,
        ok=True,
    )


# ---------------------------------------------------------------------------
# Layer A — I2V body clip (Delta 008)
# ---------------------------------------------------------------------------


@dataclass
class I2VBodyResult:
    """Outcome of a successful I2V body clip render.

    Attributes
    ----------
    body_mp4:
        Local path to the 10-second body clip written to ``tmp_dir``.
    duration_s:
        Actual clip duration reported by the provider.
    provider_used:
        Canonical provider identifier.
    tts_path:
        Local path to the narration mp3, or ``None`` if TTS failed.
    """

    body_mp4: Path
    duration_s: float
    provider_used: str
    tts_path: str | None


async def run_i2v(
    *,
    scene: Scene,
    chapter_id: int,
    image_url: str,
    i2v_router: ImageToVideoProviderRouter,
    uploader: R2Uploader,
    tts_voice: str,
    placeholder_bytes: bytes,
    tmp_dir: Path,
    duration_s: float = 10.0,
) -> I2VBodyResult:
    """Render the 10-second I2V body clip and synthesize narration TTS.

    Parameters
    ----------
    scene:
        The single ``Scene`` from ``ScriptwriterResponseV3`` carrying
        the motion prompt and narration text.
    chapter_id:
        Internal chapter id (for seed derivation only).
    image_url:
        Public HTTPS URL of the winner character's photo (R2 CDN URL).
    i2v_router:
        Pre-configured I2V provider router.
    uploader:
        Pre-configured R2 uploader (reserved for future multi-part uploads;
        the body clip is embedded in the Layer-A stitch, not uploaded solo).
    tts_voice:
        edge-tts voice name.
    placeholder_bytes:
        Fallback MP4 bytes if the I2V provider fails.
    tmp_dir:
        Temporary directory for this chapter's generation run. Must exist.
    duration_s:
        Requested I2V clip duration (default 10 s).

    Returns
    -------
    I2VBodyResult
        ``body_mp4`` always points to a valid local file.  If the I2V provider
        failed, ``body_mp4`` contains placeholder bytes (caller can still stitch
        but should log a warning).

    Raises
    ------
    I2VProviderError
        Only propagated when the error is a misconfiguration (the router
        already implements its own fallback chain).  Normal exhaustion is
        caught here and returns placeholder bytes instead.
    """
    seed = stable_hash(chapter_id, 0)
    body_tmp = tmp_dir / "i2v_body.mp4"

    logger.info(
        "i2v_render_started chapter_id=%d seed=%d duration_s=%.1f",
        chapter_id,
        seed,
        duration_s,
    )

    req = I2VRequest(
        image_url=image_url,
        motion_prompt=scene.visual_prompt,
        duration_s=duration_s,
        aspect="9:16",
        seed=seed,
    )

    ok = True
    provider_used = "placeholder"
    actual_duration = 0.0

    try:
        i2v_result: I2VResult = await i2v_router.generate(req)
        body_tmp.write_bytes(i2v_result.bytes_)
        provider_used = i2v_result.provider
        actual_duration = i2v_result.duration_s
        logger.info(
            "i2v_render_done provider=%s latency_ms=%d duration_s=%.1f",
            i2v_result.provider,
            i2v_result.latency_ms,
            i2v_result.duration_s,
        )
    except I2VProviderError:
        logger.warning("i2v_render_failed reason=all_providers_exhausted chapter_id=%d", chapter_id)
        body_tmp.write_bytes(placeholder_bytes)
        ok = False

    # TTS — best-effort; failure does NOT block the Layer-A path
    tts_path: str | None = None
    tts_bytes = await synthesize(scene.narration, voice=tts_voice)
    if tts_bytes is not None:
        audio_tmp = tmp_dir / "audio_i2v.mp3"
        try:
            audio_tmp.write_bytes(tts_bytes)
            tts_path = str(audio_tmp)
            logger.info("i2v_tts_done ok=True")
        except OSError:
            logger.warning("i2v_tts_write_failed")
    else:
        logger.info("i2v_tts_done ok=False")

    return I2VBodyResult(
        body_mp4=body_tmp,
        duration_s=actual_duration if ok else duration_s,
        provider_used=provider_used,
        tts_path=tts_path,
    )
