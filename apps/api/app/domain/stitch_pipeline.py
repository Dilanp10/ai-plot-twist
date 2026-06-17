"""Stitch pipeline — ffmpeg concat + audio mix → final chapter mp4.

Module 008 / Task T-016 (NEW).

Takes a list of settled :class:`ClipResult` (each with a local mp4 file and
an optional mp3 audio file) and produces ONE final mp4 with:

  1. Video track  — concat of all clip mp4s via ffmpeg `concat` demuxer
                    (no re-encode, ``-c copy``).
  2. Audio track  — concat of per-clip mp3 segments; clips without TTS get
                    a silent segment of length ``CLIP_DURATION_S``.
  3. Mux video + audio into ``chapter.mp4`` (cut at video end via ``shortest``).
  4. Upload the chapter mp4 to R2 at
     ``seasons/{slug}/{chapter_public_id}/chapter-{sha256[:8]}.mp4``.

All ffmpeg calls go through the synchronous ``ffmpeg-python`` library
wrapped in :func:`asyncio.to_thread` so the event loop is not blocked while
ffmpeg runs as a subprocess.

Failure surface:

- Any non-zero ffmpeg exit, or any inability to read the resulting bytes,
  raises :class:`StitchError`.
- The coordinator (T-010) catches :class:`StitchError` and triggers the
  T2I fallback path (FR-018).

Temp directory cleanup is the coordinator's responsibility (it owns the
directory); this module only writes intermediate files into ``tmp_dir``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import ffmpeg  # type: ignore[import-untyped]

from app.domain.clip_pipeline import ClipResult
from app.infra.r2_uploader import R2Uploader, R2UploadError

logger = logging.getLogger(__name__)

# Default clip duration in seconds — used to size silent fallback segments
# for clips whose TTS failed. Mirrors CLIP_DURATION_S in the coordinator.
_DEFAULT_CLIP_DURATION_S = 5.0


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StitchResult:
    """Outcome of a successful stitch run.

    Attributes
    ----------
    video_url:
        R2 public URL of the final chapter mp4.
    video_duration_s:
        Total duration of the stitched mp4 (sum of clip durations as
        reported by the providers; ffmpeg ``-c copy`` does not change
        duration).
    video_bytes_len:
        Size of the final mp4 in bytes (for logging / observability).
    """

    video_url: str
    video_duration_s: float
    video_bytes_len: int


class StitchError(Exception):
    """Raised when ffmpeg fails or the stitched output cannot be read."""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def stitch_clips(
    *,
    clips: list[ClipResult],
    tmp_dir: Path,
    uploader: R2Uploader,
    season_slug: str,
    chapter_public_id: UUID,
    clip_duration_s: float = _DEFAULT_CLIP_DURATION_S,
) -> StitchResult:
    """Stitch *clips* into a single chapter mp4 and upload it to R2.

    Parameters
    ----------
    clips:
        Settled clip list from :func:`render_clip`. Ordered by ``idx``;
        the function re-sorts defensively. Each ``ClipResult.clip_path``
        must exist on disk in *tmp_dir*.
    tmp_dir:
        Temporary directory for ffmpeg intermediate files. Must already
        exist. Cleanup is the caller's responsibility.
    uploader:
        Pre-configured R2 uploader.
    season_slug:
        URL-safe season identifier; used in the R2 path.
    chapter_public_id:
        Chapter's public UUID; used in the R2 path.
    clip_duration_s:
        Default per-clip duration used to size silent audio segments for
        clips whose TTS failed.

    Raises
    ------
    StitchError
        Any ffmpeg subprocess error, missing intermediate file, or R2
        upload error.
    """
    if not clips:
        raise StitchError("stitch_clips called with empty clip list")

    sorted_clips = sorted(clips, key=lambda c: c.idx)

    clips_with_audio = sum(1 for c in sorted_clips if c.tts_path is not None)
    clips_without_audio = len(sorted_clips) - clips_with_audio
    logger.info(
        "stitch_started clip_count=%d clips_with_audio=%d clips_without_audio=%d",
        len(sorted_clips),
        clips_with_audio,
        clips_without_audio,
    )

    video_only = tmp_dir / "video_only.mp4"
    audio_track = tmp_dir / "audio_track.mp3"
    chapter_mp4 = tmp_dir / "chapter.mp4"

    try:
        await asyncio.to_thread(
            _concat_video_clips, sorted_clips, tmp_dir, video_only
        )
        await asyncio.to_thread(
            _concat_audio_segments,
            sorted_clips,
            tmp_dir,
            audio_track,
            clip_duration_s,
        )
        await asyncio.to_thread(_mux_video_audio, video_only, audio_track, chapter_mp4)
    except ffmpeg.Error as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        logger.warning("stitch_ffmpeg_error stderr=%s", stderr[:500])
        raise StitchError(f"ffmpeg failed: {stderr[:500]}") from exc
    except FileNotFoundError as exc:
        raise StitchError(f"intermediate file missing: {exc}") from exc

    if not chapter_mp4.exists():
        raise StitchError("ffmpeg produced no output file")

    chapter_bytes = chapter_mp4.read_bytes()
    if not chapter_bytes:
        raise StitchError("ffmpeg produced empty output file")

    digest = hashlib.sha256(chapter_bytes).hexdigest()[:8]
    r2_key = (
        f"seasons/{season_slug}/{chapter_public_id}/chapter-{digest}.mp4"
    )

    try:
        video_url = await uploader.upload(r2_key, chapter_bytes, "video/mp4")
    except R2UploadError as exc:
        raise StitchError(f"R2 upload failed: {exc}") from exc

    total_duration = sum(c.duration_s for c in sorted_clips)

    logger.info(
        "stitch_done video_url=%s video_duration_s=%.1f video_bytes_len=%d",
        video_url,
        total_duration,
        len(chapter_bytes),
    )

    return StitchResult(
        video_url=video_url,
        video_duration_s=total_duration,
        video_bytes_len=len(chapter_bytes),
    )


# ---------------------------------------------------------------------------
# Internal ffmpeg steps — synchronous, run inside asyncio.to_thread
# ---------------------------------------------------------------------------


def _concat_video_clips(
    clips: list[ClipResult],
    tmp_dir: Path,
    out_path: Path,
) -> None:
    """Concat clip mp4s into a single video-only mp4 (stream copy, no re-encode)."""
    clips_list = tmp_dir / "clips_list.txt"
    lines = [f"file '{Path(c.clip_path).as_posix()}'" for c in clips]
    clips_list.write_text("\n".join(lines) + "\n", encoding="utf-8")

    (
        ffmpeg
        .input(str(clips_list), format="concat", safe=0)
        .output(str(out_path), c="copy")
        .run(quiet=True, overwrite_output=True)
    )


def _concat_audio_segments(
    clips: list[ClipResult],
    tmp_dir: Path,
    out_path: Path,
    clip_duration_s: float,
) -> None:
    """Concat per-clip audio segments; silence-pad missing ones.

    For each clip in order:
      - If ``tts_path`` exists → use the mp3 file.
      - Else → use an ``anullsrc`` silent source sized to ``clip_duration_s``.

    The output is a single mp3 covering the full chapter timeline.
    """
    inputs: list[Any] = []
    for clip in clips:
        if clip.tts_path is not None and Path(clip.tts_path).exists():
            inputs.append(ffmpeg.input(clip.tts_path))
        else:
            silence = ffmpeg.input(
                "anullsrc=channel_layout=stereo:sample_rate=44100",
                f="lavfi",
                t=clip_duration_s,
            )
            inputs.append(silence)

    (
        ffmpeg
        .concat(*inputs, v=0, a=1)
        .output(str(out_path))
        .run(quiet=True, overwrite_output=True)
    )


def _mux_video_audio(
    video_path: Path,
    audio_path: Path,
    out_path: Path,
) -> None:
    """Mux video-only mp4 + audio mp3 into final chapter mp4 (video cut wins)."""
    video = ffmpeg.input(str(video_path))
    audio = ffmpeg.input(str(audio_path))
    (
        ffmpeg
        .output(
            video,
            audio,
            str(out_path),
            vcodec="copy",
            acodec="aac",
            shortest=None,
        )
        .run(quiet=True, overwrite_output=True)
    )
