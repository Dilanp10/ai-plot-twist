"""TTS synthesizer — edge-tts wrapper (best-effort, never raises).

Module 008 / Task T-007.

Wraps the ``edge-tts`` library to produce MP3 bytes from Spanish text.
The synthesizer is intentionally best-effort: any failure (network,
provider, encoding) is caught, logged, and returns ``None``. Callers
must handle ``None`` gracefully — a panel without TTS is still valid
(``ready`` status, not ``ready_degraded``).

Default voice: ``es-AR-ElenaNeural`` (Argentine Spanish, per FR-005).
"""

from __future__ import annotations

import asyncio
import io
import logging

import edge_tts

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "es-AR-ElenaNeural"


async def synthesize(
    text: str,
    voice: str = DEFAULT_VOICE,
) -> bytes | None:
    """Synthesize *text* to MP3 bytes using edge-tts.

    Returns the raw MP3 bytes on success, or ``None`` on any failure.
    Never raises.

    Parameters
    ----------
    text:
        Spanish text to synthesize (narration or tts_text from the panel).
    voice:
        edge-tts voice name. Defaults to ``es-AR-ElenaNeural``.

    Returns
    -------
    bytes | None
        MP3 bytes, or ``None`` if synthesis fails for any reason.
    """
    if not text.strip():
        logger.warning("tts_synthesize_skipped reason=empty_text")
        return None

    try:
        communicate = edge_tts.Communicate(text, voice)
        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        mp3_bytes = buf.getvalue()
        if not mp3_bytes:
            logger.warning("tts_synthesize_empty_output voice=%s text_len=%d", voice, len(text))
            return None
        return mp3_bytes

    except asyncio.CancelledError:
        raise  # propagate cancellation — pipeline deadline uses this

    except Exception as exc:
        logger.warning(
            "tts_synthesize_failed voice=%s text_len=%d error=%s: %s",
            voice,
            len(text),
            type(exc).__name__,
            exc,
        )
        return None
