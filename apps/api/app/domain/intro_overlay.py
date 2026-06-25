"""Intro overlay renderer — ffmpeg drawtext over a static background image.

Delta 008.

Takes ``assets/intro_bg.png`` (a 9:16 background) and renders a 2-second MP4
with the episode cliffhanger text drawn over it using ``ffmpeg -vf drawtext``.

The output is a local temp file; the caller (``run_generation_pipeline``) owns
the temp directory and cleanup.

All ffmpeg calls go through :func:`asyncio.to_thread` to keep the event loop
unblocked.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import ffmpeg  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


class IntroRenderError(Exception):
    """ffmpeg failed to render the intro clip."""


def _render_intro_sync(
    *,
    bg_path: Path,
    out_path: Path,
    text: str,
    duration_s: float,
    font_size: int,
    font_color: str,
    width: int = 1080,
    height: int = 1920,
    fps: int = 24,
) -> None:
    """Blocking ffmpeg call — run inside :func:`asyncio.to_thread`."""
    # Escape special characters for ffmpeg drawtext
    escaped = (
        text
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )

    drawtext_filter = (
        f"drawtext=text='{escaped}'"
        f":fontsize={font_size}"
        f":fontcolor={font_color}"
        f":x=(w-text_w)/2"
        f":y=(h-text_h)/2"
        f":line_spacing=8"
        f":expansion=none"
    )

    try:
        (
            ffmpeg
            .input(
                str(bg_path),
                loop=1,
                t=duration_s,
                framerate=fps,
            )
            .filter("scale", width, height)
            .filter("fps", fps=fps)
            .filter_complex(f"[0:v]{drawtext_filter}[v]")
            .output(
                str(out_path),
                map="[v]",
                vcodec="libx264",
                pix_fmt="yuv420p",
                t=duration_s,
                r=fps,
            )
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
    except ffmpeg.Error as exc:
        stderr = exc.stderr.decode(errors="replace") if exc.stderr else ""
        raise IntroRenderError(f"ffmpeg intro render failed: {stderr[:500]}") from exc


async def render_intro(
    *,
    bg_path: Path,
    out_path: Path,
    text: str,
    duration_s: float = 2.0,
    font_size: int = 64,
    font_color: str = "white",
    width: int = 1080,
    height: int = 1920,
    fps: int = 24,
) -> None:
    """Async wrapper around :func:`_render_intro_sync`.

    Parameters
    ----------
    bg_path:
        Path to the background PNG (e.g. ``assets/intro_bg.png``).
    out_path:
        Destination path for the rendered MP4 (e.g. ``tmp/intro.mp4``).
    text:
        Cliffhanger text to draw (``ScriptwriterResponseV3.cliffhanger``).
    duration_s:
        Duration of the intro clip in seconds.
    font_size:
        Font size in pixels for the drawtext filter.
    font_color:
        Color string accepted by ffmpeg (e.g. ``"white"``, ``"#ffffff"``).
    width / height:
        Output resolution. Default 1080x1920 (9:16 portrait).
    fps:
        Frames per second.

    Raises
    ------
    IntroRenderError
        When ffmpeg exits non-zero or any filesystem error occurs.
    """
    logger.info(
        "intro_render_start out=%s duration_s=%.1f text_len=%d",
        out_path,
        duration_s,
        len(text),
    )
    await asyncio.to_thread(
        _render_intro_sync,
        bg_path=bg_path,
        out_path=out_path,
        text=text,
        duration_s=duration_s,
        font_size=font_size,
        font_color=font_color,
        width=width,
        height=height,
        fps=fps,
    )
    logger.info("intro_render_done out=%s", out_path)
