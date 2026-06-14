"""Unit tests: tts_synthesizer.

Module 008 / Task T-007.

All tests mock edge_tts.Communicate so no real network calls are made.

Coverage:
  - Successful synthesis returns non-empty bytes.
  - Empty text returns None without calling edge-tts.
  - Whitespace-only text returns None without calling edge-tts.
  - edge-tts raises an exception → returns None (best-effort).
  - edge-tts yields no audio chunks → returns None.
  - asyncio.CancelledError propagates (not swallowed).
  - Custom voice is forwarded to Communicate.
  - Default voice is es-AR-ElenaNeural.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from app.domain.tts_synthesizer import DEFAULT_VOICE, synthesize

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_communicate(chunks: list[dict]) -> MagicMock:
    """Build a mock Communicate that yields *chunks* from .stream()."""
    mock = MagicMock()

    async def _stream():
        for c in chunks:
            yield c

    mock.stream.return_value = _stream()
    return mock


def _audio_chunks(n: int = 3) -> list[dict]:
    return [{"type": "audio", "data": b"mp3data"} for _ in range(n)]


def _mixed_chunks() -> list[dict]:
    return [
        {"type": "WordBoundary", "text": "hola"},
        {"type": "audio", "data": b"chunk1"},
        {"type": "SentenceBoundary", "text": "hola mundo"},
        {"type": "audio", "data": b"chunk2"},
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_returns_bytes_on_success() -> None:
    communicate = _make_communicate(_audio_chunks())
    with patch("edge_tts.Communicate", return_value=communicate):
        result = await synthesize("Hola mundo")

    assert isinstance(result, bytes)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_synthesize_concatenates_all_audio_chunks() -> None:
    communicate = _make_communicate(_audio_chunks(3))
    with patch("edge_tts.Communicate", return_value=communicate):
        result = await synthesize("Texto de prueba")

    assert result == b"mp3data" * 3


@pytest.mark.asyncio
async def test_synthesize_skips_non_audio_chunks() -> None:
    communicate = _make_communicate(_mixed_chunks())
    with patch("edge_tts.Communicate", return_value=communicate):
        result = await synthesize("Hola mundo")

    assert result == b"chunk1chunk2"


@pytest.mark.asyncio
async def test_synthesize_empty_text_returns_none() -> None:
    with patch("edge_tts.Communicate") as mock_cls:
        result = await synthesize("")

    assert result is None
    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_synthesize_whitespace_only_returns_none() -> None:
    with patch("edge_tts.Communicate") as mock_cls:
        result = await synthesize("   \n\t  ")

    assert result is None
    mock_cls.assert_not_called()


@pytest.mark.asyncio
async def test_synthesize_exception_returns_none() -> None:
    mock_comm = MagicMock()

    async def _failing_stream():
        raise ConnectionError("edge-tts unreachable")
        yield  # make it a generator

    mock_comm.stream.return_value = _failing_stream()
    with patch("edge_tts.Communicate", return_value=mock_comm):
        result = await synthesize("Texto que falla")

    assert result is None


@pytest.mark.asyncio
async def test_synthesize_no_audio_chunks_returns_none() -> None:
    communicate = _make_communicate([
        {"type": "WordBoundary", "text": "hola"},
        {"type": "SentenceBoundary", "text": "."},
    ])
    with patch("edge_tts.Communicate", return_value=communicate):
        result = await synthesize("Hola")

    assert result is None


@pytest.mark.asyncio
async def test_synthesize_cancelled_error_propagates() -> None:
    """CancelledError must NOT be swallowed — the pipeline deadline relies on it."""
    mock_comm = MagicMock()

    async def _cancelled_stream():
        raise asyncio.CancelledError
        yield  # make it a generator

    mock_comm.stream.return_value = _cancelled_stream()
    with (
        patch("edge_tts.Communicate", return_value=mock_comm),
        pytest.raises(asyncio.CancelledError),
    ):
        await synthesize("Texto cancelado")


@pytest.mark.asyncio
async def test_synthesize_forwards_voice_to_communicate() -> None:
    communicate = _make_communicate(_audio_chunks())
    with patch("edge_tts.Communicate", return_value=communicate) as mock_cls:
        await synthesize("Texto", voice="es-MX-DaliaNeural")

    mock_cls.assert_called_once_with("Texto", "es-MX-DaliaNeural")


@pytest.mark.asyncio
async def test_synthesize_default_voice_is_argentina() -> None:
    assert DEFAULT_VOICE == "es-AR-ElenaNeural"

    communicate = _make_communicate(_audio_chunks())
    with patch("edge_tts.Communicate", return_value=communicate) as mock_cls:
        await synthesize("Hola")

    mock_cls.assert_called_once_with("Hola", "es-AR-ElenaNeural")
