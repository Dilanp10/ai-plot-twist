"""Live smoke: GeminiProvider → real Gemini API (T-013).

Marked ``@pytest.mark.live``. Skipped automatically when GEMINI_API_KEY
is absent (e.g. in CI). Excluded from the regular test run via
``-m "not live"`` in ci.yml.

Run manually::

    uv run pytest -m live -v
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.domain.director_context import (
    ChapterBrief,
    CurrentChapterInput,
    DirectorContext,
    SeasonInput,
    TwistInput,
)
from app.domain.director_prompts import load_system_prompt, render_user_prompt
from app.domain.director_verdicts import DirectorBatchResponse
from app.providers.llm.gemini import GeminiProvider

pytestmark = pytest.mark.live

# ---------------------------------------------------------------------------
# Synthetic batch — 3 twists with different expected verdicts
# ---------------------------------------------------------------------------

_IDS = [uuid4(), uuid4(), uuid4()]
_CTX = DirectorContext(
    season=SeasonInput(
        bible_json={
            "title": "Sombras del Pasado",
            "genre": "Drama",
            "logline": "Una familia descubre que sus secretos enterrados siguen vivos.",
        }
    ),
    last_chapters=[
        ChapterBrief(
            day_index=1,
            title="El regreso",
            synopsis="La familia vuelve a la ciudad natal después de veinte años.",
        ),
    ],
    current=CurrentChapterInput(
        day_index=2,
        title="El sobre",
        synopsis="Un sobre anónimo llega a la puerta con fotografías del pasado.",
        manifest_json={"cliffhanger": "¿Quién envió el sobre y qué quiere revelar?"},
    ),
    batch=[
        TwistInput(
            public_id=_IDS[0],
            content="El sobre lo mandó el hermano que desapareció hace diez años.",
        ),
        TwistInput(
            public_id=_IDS[1],
            content="La madre reconoció la letra pero fingió no saber nada.",
        ),
        TwistInput(
            public_id=_IDS[2],
            content="asdfjkl zxcvbnm 123 qwerty",
        ),
    ],
)

_VALID_DECISIONS = {"approved", "rejected_offensive", "rejected_incoherent", "rejected_spam"}


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


async def test_gemini_classifies_synthetic_batch() -> None:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        pytest.skip("GEMINI_API_KEY not set")

    provider = GeminiProvider(api_key=key)
    resp = await provider.chat_json(
        system=load_system_prompt(),
        user=render_user_prompt(_CTX),
        response_schema=DirectorBatchResponse,
    )

    batch: DirectorBatchResponse = resp.content  # type: ignore[assignment]
    assert len(batch.verdicts) > 0, "Gemini returned an empty verdicts list"
    for v in batch.verdicts:
        assert v.decision in _VALID_DECISIONS, f"Unknown decision: {v.decision!r}"
        assert 1 <= len(v.reason) <= 80, f"Reason out of bounds: {v.reason!r}"
    assert resp.tokens_in > 0, "tokens_in should be positive"
    assert resp.tokens_out > 0, "tokens_out should be positive"
