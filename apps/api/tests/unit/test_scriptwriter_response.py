"""Unit tests: scriptwriter_response Pydantic models.

Module 008 / Task T-003.

All tests are pure Python — no database, no I/O.

Coverage:
  Panel:
    - valid construction
    - visual_prompt: < 80 % ASCII printable → rejected
    - visual_prompt: > 80 % ASCII printable (mixed, mostly English) → accepted
    - visual_prompt: pure ASCII → accepted
    - visual_prompt: too short → rejected
    - field lengths enforced (narration, tts_text, visual_prompt)

  ScriptwriterResponse:
    - valid 3-panel response
    - valid 4-panel response
    - panels not contiguous from 1 → rejected
    - panels with duplicate idx → rejected
    - panels count < 3 → rejected
    - panels count > 4 → rejected
    - field lengths enforced (title, synopsis, cliffhanger)

  WinnerMetadata:
    - auto-continue mode (all nulls / defaults)
    - normal mode with tiebreak
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.scriptwriter_response import (
    Panel,
    ScriptwriterResponse,
    WinnerMetadata,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_GOOD_VISUAL = (
    "a woman reaches into a fractured antique mirror, "
    "cinematic lighting, 35mm film, moody, no text"
)
_GOOD_NARRATION = "El espejo crujió como hielo viejo."
_GOOD_TTS = "El espejo crujió como hielo viejo."
_GOOD_TITLE = "Lo que había detrás del espejo"
_GOOD_SYNOPSIS = "Mariana acepta la propuesta del reflejo y descubre su otra-yo."
_GOOD_CLIFF = "Entonces escuchó la voz de su madre."
_GOOD_SEED = "La madre del 1998 alterno está viva pero algo no es humano."


def _panel(idx: int = 1, visual_prompt: str = _GOOD_VISUAL) -> Panel:
    return Panel(
        idx=idx,
        narration=_GOOD_NARRATION,
        visual_prompt=visual_prompt,
        mood="tense",
        tts_text=_GOOD_TTS,
    )


def _response_3() -> ScriptwriterResponse:
    return ScriptwriterResponse(
        title=_GOOD_TITLE,
        synopsis=_GOOD_SYNOPSIS,
        panels=[_panel(1), _panel(2), _panel(3)],
        cliffhanger=_GOOD_CLIFF,
        next_cliffhanger_seed=_GOOD_SEED,
    )


# ---------------------------------------------------------------------------
# Panel tests
# ---------------------------------------------------------------------------


def test_panel_valid() -> None:
    p = _panel()
    assert p.idx == 1
    assert p.mood == "tense"


def test_panel_visual_prompt_pure_ascii_accepted() -> None:
    p = _panel(
        visual_prompt="cinematic shot of a woman in a Buenos Aires apartment, 35mm, moody"
    )
    assert p.visual_prompt.startswith("cinematic")


def test_panel_visual_prompt_mostly_ascii_accepted() -> None:
    # A few accented chars within an otherwise English prompt (< 20 % non-ASCII)
    prompt = "a woman in a Buenos Aires apartment — moody lighting, 35mm film, cinematic, no text"
    p = _panel(visual_prompt=prompt)
    assert p.visual_prompt == prompt


def test_panel_visual_prompt_mostly_non_ascii_rejected() -> None:
    # Build a string that is > 20 % non-ASCII printable (80 % non-ASCII chars).
    # Validator threshold: printable_ascii / total <= 0.80 → reject.
    non_ascii_heavy = "ñ" * 80 + "a" * 20  # 80 % non-ASCII → should reject
    assert len(non_ascii_heavy) == 100
    with pytest.raises(ValidationError) as exc_info:
        _panel(visual_prompt=non_ascii_heavy)
    assert "ASCII printable" in str(exc_info.value)


def test_panel_visual_prompt_too_short_rejected() -> None:
    with pytest.raises(ValidationError):
        _panel(visual_prompt="short")


def test_panel_narration_too_short_rejected() -> None:
    with pytest.raises(ValidationError):
        Panel(
            idx=1,
            narration="x" * 9,  # min_length=10
            visual_prompt=_GOOD_VISUAL,
            mood="tense",
            tts_text=_GOOD_TTS,
        )


def test_panel_narration_too_long_rejected() -> None:
    with pytest.raises(ValidationError):
        Panel(
            idx=1,
            narration="x" * 501,
            visual_prompt=_GOOD_VISUAL,
            mood="tense",
            tts_text=_GOOD_TTS,
        )


def test_panel_invalid_mood_rejected() -> None:
    with pytest.raises(ValidationError):
        Panel(
            idx=1,
            narration=_GOOD_NARRATION,
            visual_prompt=_GOOD_VISUAL,
            mood="furious",  # type: ignore[arg-type]
            tts_text=_GOOD_TTS,
        )


def test_panel_all_moods_accepted() -> None:
    moods = [
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
    for mood in moods:
        p = Panel(
            idx=1,
            narration=_GOOD_NARRATION,
            visual_prompt=_GOOD_VISUAL,
            mood=mood,  # type: ignore[arg-type]
            tts_text=_GOOD_TTS,
        )
        assert p.mood == mood


# ---------------------------------------------------------------------------
# ScriptwriterResponse tests
# ---------------------------------------------------------------------------


def test_response_valid_3_panels() -> None:
    r = _response_3()
    assert len(r.panels) == 3
    assert [p.idx for p in r.panels] == [1, 2, 3]


def test_response_valid_4_panels() -> None:
    r = ScriptwriterResponse(
        title=_GOOD_TITLE,
        synopsis=_GOOD_SYNOPSIS,
        panels=[_panel(1), _panel(2), _panel(3), _panel(4)],
        cliffhanger=_GOOD_CLIFF,
        next_cliffhanger_seed=_GOOD_SEED,
    )
    assert len(r.panels) == 4


def test_response_non_contiguous_panels_rejected() -> None:
    # idx 1, 2, 4 — skips 3
    with pytest.raises(ValidationError) as exc_info:
        ScriptwriterResponse(
            title=_GOOD_TITLE,
            synopsis=_GOOD_SYNOPSIS,
            panels=[_panel(1), _panel(2), _panel(4)],
            cliffhanger=_GOOD_CLIFF,
            next_cliffhanger_seed=_GOOD_SEED,
        )
    assert "contiguous" in str(exc_info.value)


def test_response_duplicate_idx_rejected() -> None:
    # idx [1, 1, 2] is not [1, 2, 3]
    with pytest.raises(ValidationError):
        ScriptwriterResponse(
            title=_GOOD_TITLE,
            synopsis=_GOOD_SYNOPSIS,
            panels=[_panel(1), _panel(1), _panel(2)],
            cliffhanger=_GOOD_CLIFF,
            next_cliffhanger_seed=_GOOD_SEED,
        )


def test_response_too_few_panels_rejected() -> None:
    with pytest.raises(ValidationError):
        ScriptwriterResponse(
            title=_GOOD_TITLE,
            synopsis=_GOOD_SYNOPSIS,
            panels=[_panel(1), _panel(2)],
            cliffhanger=_GOOD_CLIFF,
            next_cliffhanger_seed=_GOOD_SEED,
        )


def test_response_too_many_panels_rejected() -> None:
    with pytest.raises(ValidationError):
        ScriptwriterResponse(
            title=_GOOD_TITLE,
            synopsis=_GOOD_SYNOPSIS,
            panels=[_panel(i) for i in range(1, 6)],  # 5 panels
            cliffhanger=_GOOD_CLIFF,
            next_cliffhanger_seed=_GOOD_SEED,
        )


def test_response_title_too_short_rejected() -> None:
    with pytest.raises(ValidationError):
        ScriptwriterResponse(
            title="Hi",  # min_length=5
            synopsis=_GOOD_SYNOPSIS,
            panels=[_panel(1), _panel(2), _panel(3)],
            cliffhanger=_GOOD_CLIFF,
            next_cliffhanger_seed=_GOOD_SEED,
        )


def test_response_synopsis_too_short_rejected() -> None:
    with pytest.raises(ValidationError):
        ScriptwriterResponse(
            title=_GOOD_TITLE,
            synopsis="Corto.",  # min_length=20
            panels=[_panel(1), _panel(2), _panel(3)],
            cliffhanger=_GOOD_CLIFF,
            next_cliffhanger_seed=_GOOD_SEED,
        )


# ---------------------------------------------------------------------------
# WinnerMetadata tests
# ---------------------------------------------------------------------------


def test_winner_metadata_auto_continue_defaults() -> None:
    wm = WinnerMetadata()
    assert wm.winner_twist_id is None
    assert wm.winner_author_display_name is None
    assert wm.vote_count == 0
    assert wm.tiebreak is False
    assert wm.runner_up_twist_id is None


def test_winner_metadata_with_tiebreak() -> None:
    uid1 = uuid4()
    uid2 = uuid4()
    wm = WinnerMetadata(
        winner_twist_id=uid1,
        winner_author_display_name="Alice",
        vote_count=5,
        tiebreak=True,
        runner_up_twist_id=uid2,
    )
    assert wm.tiebreak is True
    assert wm.runner_up_twist_id == uid2


def test_winner_metadata_normal_no_tiebreak() -> None:
    uid = uuid4()
    wm = WinnerMetadata(
        winner_twist_id=uid,
        winner_author_display_name="Bob",
        vote_count=12,
        tiebreak=False,
    )
    assert wm.winner_twist_id == uid
    assert wm.runner_up_twist_id is None
