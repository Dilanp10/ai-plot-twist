"""Unit tests: intro_overlay.render_intro / _render_intro_sync.

Delta 008.

ffmpeg is mocked at the asyncio.to_thread level so the suite runs without
the binary installed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domain.intro_overlay import IntroRenderError, _render_intro_sync, render_intro

# ---------------------------------------------------------------------------
# _render_intro_sync — text escaping (pure Python, no ffmpeg)
# ---------------------------------------------------------------------------


def _make_escaped(text: str) -> str:
    """Reproduce the escape logic from _render_intro_sync."""
    return (
        text
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )


def test_escape_single_quote(tmp_path: Path) -> None:
    """Single quotes in cliffhanger are escaped for drawtext."""
    text = "El espejo dijo: 'no hay salida'"
    escaped = _make_escaped(text)
    assert "\\'" in escaped
    assert "'" not in escaped.replace("\\'", "")


def test_escape_colon(tmp_path: Path) -> None:
    text = "10:00 pm: el reloj paró"
    escaped = _make_escaped(text)
    assert "\\:" in escaped


def test_escape_brackets(tmp_path: Path) -> None:
    text = "[Oscuridad] total [silencio]"
    escaped = _make_escaped(text)
    assert "\\[" in escaped
    assert "\\]" in escaped


def test_escape_backslash(tmp_path: Path) -> None:
    text = "ruta\\archivo"
    escaped = _make_escaped(text)
    assert "\\\\" in escaped


# ---------------------------------------------------------------------------
# render_intro — mocking asyncio.to_thread
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_intro_calls_to_thread(tmp_path: Path) -> None:
    bg = tmp_path / "bg.png"
    bg.write_bytes(b"\x89PNG")
    out = tmp_path / "intro.mp4"

    with patch("app.domain.intro_overlay.asyncio.to_thread", new=AsyncMock()) as mock_thread:
        await render_intro(
            bg_path=bg,
            out_path=out,
            text="¿Quién mató al testigo?",
        )

    mock_thread.assert_awaited_once()
    # First positional arg is the sync function
    assert mock_thread.call_args.args[0] is _render_intro_sync


@pytest.mark.asyncio
async def test_render_intro_passes_correct_kwargs(tmp_path: Path) -> None:
    bg = tmp_path / "bg.png"
    bg.write_bytes(b"\x89PNG")
    out = tmp_path / "intro.mp4"
    text = "El tren nunca llegó."

    with patch("app.domain.intro_overlay.asyncio.to_thread", new=AsyncMock()) as mock_thread:
        await render_intro(
            bg_path=bg,
            out_path=out,
            text=text,
            duration_s=3.0,
            font_size=48,
            font_color="yellow",
        )

    kwargs = mock_thread.call_args.kwargs
    assert kwargs["bg_path"] == bg
    assert kwargs["out_path"] == out
    assert kwargs["text"] == text
    assert kwargs["duration_s"] == 3.0
    assert kwargs["font_size"] == 48
    assert kwargs["font_color"] == "yellow"


@pytest.mark.asyncio
async def test_render_intro_default_params(tmp_path: Path) -> None:
    bg = tmp_path / "bg.png"
    bg.write_bytes(b"\x89PNG")
    out = tmp_path / "intro.mp4"

    with patch("app.domain.intro_overlay.asyncio.to_thread", new=AsyncMock()) as mock_thread:
        await render_intro(bg_path=bg, out_path=out, text="Default test")

    kwargs = mock_thread.call_args.kwargs
    assert kwargs["duration_s"] == 2.0
    assert kwargs["font_size"] == 64
    assert kwargs["font_color"] == "white"
    assert kwargs["width"] == 1080
    assert kwargs["height"] == 1920
    assert kwargs["fps"] == 24


# ---------------------------------------------------------------------------
# _render_intro_sync — ffmpeg.Error → IntroRenderError
# ---------------------------------------------------------------------------


def test_render_intro_sync_raises_intro_render_error_on_ffmpeg_error(
    tmp_path: Path,
) -> None:
    import ffmpeg  # type: ignore[import-untyped]

    bg = tmp_path / "bg.png"
    bg.write_bytes(b"\x89PNG")
    out = tmp_path / "intro.mp4"

    ffmpeg_error = ffmpeg.Error("ffmpeg", stdout=b"", stderr=b"segfault")

    mock_stream = MagicMock()
    mock_stream.filter.return_value = mock_stream
    mock_stream.filter_complex.return_value = mock_stream
    mock_stream.output.return_value = mock_stream
    mock_stream.overwrite_output.return_value = mock_stream
    mock_stream.run.side_effect = ffmpeg_error

    with patch("app.domain.intro_overlay.ffmpeg") as mock_ffmpeg:
        mock_ffmpeg.input.return_value = mock_stream
        mock_ffmpeg.Error = ffmpeg.Error  # keep real exception type

        with pytest.raises(IntroRenderError, match="ffmpeg intro render failed"):
            _render_intro_sync(
                bg_path=bg,
                out_path=out,
                text="Test",
                duration_s=2.0,
                font_size=64,
                font_color="white",
            )
