"""Pydantic models for the scriptwriter LLM output and winner metadata.

Module 008 / Task T-003.

``ScriptwriterResponse`` mirrors ``contracts/scriptwriter-response.schema.json``
and is passed as ``response_schema`` to ``LLMProvider.chat_json``. The LLM
is constrained to return JSON that Pydantic can parse into this model.

``WinnerMetadata`` is NOT produced by the LLM; it is assembled by the
pipeline coordinator from ``WinnerPick`` and stored alongside the
scriptwriter output in ``chapters.manifest_json``.

Key validation rules:
- ``visual_prompt`` must be > 80 % ASCII printable characters (R-002).
  Diffusion models perform worse on Spanish prompts; the system prompt
  already instructs English, the validator is a second line of defence.
- ``panels`` must be contiguous from idx=1 (e.g. [1,2,3] or [1,2,3,4]).
  If violated, the scriptwriter consumer (T-008) retries once; on
  second failure it renumbers server-side.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

_Mood = Literal[
    "tense",
    "ominous",
    "contemplative",
    "hopeful",
    "absurd",
    "melancholic",
    "euphoric",
    "dread",
    "tender",
]

_ASCII_PRINTABLE_LOW = 32
_ASCII_PRINTABLE_HIGH = 126
_ENGLISH_RATIO_THRESHOLD = 0.80


class Panel(BaseModel):
    idx: int = Field(..., ge=1, le=8)
    narration: str = Field(..., min_length=10, max_length=500)
    visual_prompt: str = Field(..., min_length=20, max_length=400)
    mood: _Mood
    tts_text: str = Field(..., min_length=10, max_length=500)

    @field_validator("visual_prompt")
    @classmethod
    def visual_prompt_must_be_english(cls, v: str) -> str:
        printable_count = sum(
            1 for c in v if _ASCII_PRINTABLE_LOW <= ord(c) <= _ASCII_PRINTABLE_HIGH
        )
        if len(v) > 0 and printable_count / len(v) <= _ENGLISH_RATIO_THRESHOLD:
            raise ValueError(
                "visual_prompt must be > 80 % ASCII printable characters "
                "(write it in English for best T2I quality)"
            )
        return v


# ---------------------------------------------------------------------------
# ScriptwriterResponse — LLM output schema
# ---------------------------------------------------------------------------


class ScriptwriterResponse(BaseModel):
    """Structured output returned by the scriptwriter LLM call.

    Passed as ``response_schema`` to ``LLMProvider.chat_json`` so the
    provider enforces JSON structure at the API level. The
    ``panels_are_contiguous`` validator provides a second layer of
    validation after parsing.
    """

    title: str = Field(..., min_length=5, max_length=80)
    synopsis: str = Field(..., min_length=20, max_length=400)
    panels: list[Panel] = Field(..., min_length=3, max_length=4)
    cliffhanger: str = Field(..., min_length=10, max_length=300)
    next_cliffhanger_seed: str = Field(..., min_length=10, max_length=300)

    @model_validator(mode="after")
    def panels_are_contiguous(self) -> ScriptwriterResponse:
        indices = sorted(p.idx for p in self.panels)
        expected = list(range(1, len(indices) + 1))
        if indices != expected:
            raise ValueError(f"panel idx values must be contiguous from 1: got {indices}")
        return self


# ---------------------------------------------------------------------------
# WinnerMetadata — assembled by coordinator, stored in manifest_json
# ---------------------------------------------------------------------------


class WinnerMetadata(BaseModel):
    """Transparency record stored in ``manifest_json.winner_metadata``.

    All fields are ``None`` in auto-continue mode (no approved twists).
    Module 004's serializer intentionally drops this from the public API;
    it is ops-only.
    """

    winner_twist_id: UUID | None = None
    winner_author_display_name: str | None = None
    vote_count: int = 0
    tiebreak: bool = False
    runner_up_twist_id: UUID | None = None
