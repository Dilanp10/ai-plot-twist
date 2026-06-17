"""Live smoke: end-to-end generation pipeline against real providers (T-014).

Marked ``@pytest.mark.live``. Each test skips when its required key is
absent so the suite stays useful in any partial-credentials environment.

Run manually::

    uv run pytest -m live -v tests/live/test_full_pipeline_smoke.py

Covered hops:
  - Scriptwriter via :class:`Scriptwriter` → real Gemini, asserts the
    returned :class:`ScriptwriterResponse` validates and has 3-4 panels.
  - Image router via :func:`chain_for_env('mvp')` → renders the first
    panel against Pollinations / HuggingFace.
  - TTS via :func:`synthesize` → edge-tts, best-effort (never raises).

R2 uploads are intentionally NOT exercised here — the upload path is a
thin boto3 wrapper covered by mock-based unit tests; running it in CI
would pollute the real bucket without buying extra signal.
"""

from __future__ import annotations

import os

import pytest

from app.domain.scriptwriter import Scriptwriter
from app.domain.scriptwriter_prompts import (
    ChapterBrief,
    ScriptContext,
    SeasonBrief,
)
from app.domain.scriptwriter_response import ScriptwriterResponse
from app.domain.tts_synthesizer import DEFAULT_VOICE, synthesize
from app.providers.image import ImageProviderRouter, ImageRequest, chain_for_env
from app.providers.llm.gemini import GeminiProvider
from app.providers.llm.router import LLMProviderRouter

pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# Synthetic context — same fictional season as module 006's smoke
# ---------------------------------------------------------------------------


def _build_context(*, with_winner: bool) -> ScriptContext:
    return ScriptContext(
        season=SeasonBrief(
            title="Sombras del Pasado",
            bible_json={
                "genre": "Drama",
                "logline": (
                    "Una familia descubre que sus secretos enterrados "
                    "siguen vivos."
                ),
            },
        ),
        recent_chapters=[
            ChapterBrief(
                day_index=1,
                title="El regreso",
                synopsis=(
                    "La familia vuelve a la ciudad natal después de "
                    "veinte años."
                ),
                cliffhanger="Un sobre anónimo apareció bajo la puerta.",
            ),
        ],
        current_chapter=ChapterBrief(
            day_index=2,
            title="El sobre",
            synopsis=(
                "Un sobre anónimo llega a la puerta con fotografías "
                "del pasado."
            ),
            cliffhanger="¿Quién envió el sobre y qué quiere revelar?",
        ),
        next_day_index=3,
        winner_content=(
            "El sobre lo mandó el hermano que desapareció hace diez años."
            if with_winner
            else None
        ),
    )


# ---------------------------------------------------------------------------
# 1. Scriptwriter — real Gemini
# ---------------------------------------------------------------------------


async def test_scriptwriter_drafts_valid_script_against_gemini() -> None:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        pytest.skip("GEMINI_API_KEY not set")

    router = LLMProviderRouter(
        [GeminiProvider(api_key=key)],
        check_health=False,
    )
    sw = Scriptwriter(router)

    script = await sw.draft(_build_context(with_winner=True))

    assert isinstance(script, ScriptwriterResponse)
    assert 4 <= len(script.clips) <= 6
    for c in script.clips:
        assert c.narration.strip(), f"empty narration on clip {c.idx}"
        assert c.visual_prompt.strip(), f"empty visual on clip {c.idx}"
        assert c.tts_text.strip(), f"empty tts_text on clip {c.idx}"
    assert script.cliffhanger.strip()


async def test_scriptwriter_auto_mode_against_gemini() -> None:
    """Auto-continue mode: ``winner_content=None`` triggers the alt prompt."""
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        pytest.skip("GEMINI_API_KEY not set")

    router = LLMProviderRouter(
        [GeminiProvider(api_key=key)],
        check_health=False,
    )
    sw = Scriptwriter(router)

    script = await sw.draft(_build_context(with_winner=False))

    assert 4 <= len(script.clips) <= 6
    assert script.title.strip()


# ---------------------------------------------------------------------------
# 2. Image router — real Pollinations + HuggingFace chain
# ---------------------------------------------------------------------------


async def test_image_router_renders_first_panel_against_mvp_chain() -> None:
    hf = os.getenv("HUGGINGFACE_TOKEN")
    if not hf:
        pytest.skip("HUGGINGFACE_TOKEN not set")

    chain = chain_for_env("mvp", huggingface_token=hf)
    router = ImageProviderRouter(chain, check_health=False)

    req = ImageRequest(
        prompt=(
            "a weathered wooden envelope on a dusty doormat, "
            "afternoon light, cinematic 35mm"
        ),
        seed=12345,
    )
    result = await router.render(req)

    assert result.bytes_, "image bytes are empty"
    assert result.mime_type in {"image/webp", "image/png", "image/jpeg"}
    assert result.provider in {"pollinations", "hf"}


# ---------------------------------------------------------------------------
# 3. TTS — best effort
# ---------------------------------------------------------------------------


async def test_tts_synthesizer_returns_bytes_or_none() -> None:
    """edge-tts must never raise; returns bytes on success, None on failure."""
    audio = await synthesize(
        "Hola, esta es una prueba de síntesis de voz.",
        voice=DEFAULT_VOICE,
    )
    # No skip — we want CI to flag if edge-tts changes its public surface.
    assert audio is None or isinstance(audio, bytes)
    if audio is not None:
        assert len(audio) > 1_000, "TTS bytes suspiciously small"
