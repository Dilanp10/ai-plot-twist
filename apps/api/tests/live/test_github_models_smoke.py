"""Live smoke: GitHubModelsProvider → real GitHub Models API (T-014).

Marked ``@pytest.mark.live``. Skipped automatically when neither
``GH_MODELS_TOKEN`` nor ``GITHUB_MODELS_TOKEN`` is set.

In GitHub Actions the secret is named ``GH_MODELS_TOKEN`` (GitHub forbids
secrets that start with "GITHUB"). In local dev the env var is typically
``GITHUB_MODELS_TOKEN`` (set via Fly / .env.local). Both are checked.

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
from app.providers.llm.github_models import GitHubModelsProvider

pytestmark = pytest.mark.live

# ---------------------------------------------------------------------------
# Synthetic batch — same scenario as Gemini smoke for easy comparison
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


async def test_github_models_classifies_synthetic_batch() -> None:
    # GH Actions secret is GH_MODELS_TOKEN; local dev may use GITHUB_MODELS_TOKEN.
    key = os.getenv("GH_MODELS_TOKEN") or os.getenv("GITHUB_MODELS_TOKEN")
    if not key:
        pytest.skip("GH_MODELS_TOKEN / GITHUB_MODELS_TOKEN not set")

    provider = GitHubModelsProvider(api_key=key)
    resp = await provider.chat_json(
        system=load_system_prompt(),
        user=render_user_prompt(_CTX),
        response_schema=DirectorBatchResponse,
    )

    batch: DirectorBatchResponse = resp.content  # type: ignore[assignment]
    assert len(batch.verdicts) > 0, "GH Models returned an empty verdicts list"
    for v in batch.verdicts:
        assert v.decision in _VALID_DECISIONS, f"Unknown decision: {v.decision!r}"
        assert 1 <= len(v.reason) <= 80, f"Reason out of bounds: {v.reason!r}"
    assert resp.tokens_in > 0, "tokens_in should be positive"
    assert resp.tokens_out > 0, "tokens_out should be positive"
