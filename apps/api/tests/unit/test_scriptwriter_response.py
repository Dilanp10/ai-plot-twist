"""Unit tests: scriptwriter_response Pydantic models (v2.0 — clips).

Module 008 / Task T-003 delta.

All tests are pure Python — no database, no I/O.

Coverage:
  Clip:
    - valid construction
    - visual_prompt: < 80 % ASCII printable -> rejected
    - visual_prompt: > 80 % ASCII printable (mixed, mostly English) -> accepted
    - visual_prompt: pure ASCII -> accepted
    - visual_prompt: too short -> rejected
    - field lengths enforced (narration, tts_text, visual_prompt)
    - all moods accepted
    - invalid mood rejected

  ScriptwriterResponse:
    - valid 4-clip response
    - valid 6-clip response
    - clips not contiguous from 1 -> rejected
    - clips with duplicate idx -> rejected
    - clips count < 4 -> rejected
    - clips count > 6 -> rejected
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
    Clip,
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


def _clip(idx: int = 1, visual_prompt: str = _GOOD_VISUAL) -> Clip:
    return Clip(
        idx=idx,
        narration=_GOOD_NARRATION,
        visual_prompt=visual_prompt,
        mood="tense",
        tts_text=_GOOD_TTS,
    )


def _response_4() -> ScriptwriterResponse:
    return ScriptwriterResponse(
        title=_GOOD_TITLE,
        synopsis=_GOOD_SYNOPSIS,
        clips=[_clip(1), _clip(2), _clip(3), _clip(4)],
        cliffhanger=_GOOD_CLIFF,
        next_cliffhanger_seed=_GOOD_SEED,
    )


# ---------------------------------------------------------------------------
# Clip tests
# ---------------------------------------------------------------------------


def test_clip_valid() -> None:
    c = _clip()
    assert c.idx == 1
    assert c.mood == "tense"


def test_clip_visual_prompt_pure_ascii_accepted() -> None:
    c = _clip(
        visual_prompt="cinematic shot of a woman in a Buenos Aires apartment, 35mm, moody"
    )
    assert c.visual_prompt.startswith("cinematic")


def test_clip_visual_prompt_mostly_ascii_accepted() -> None:
    prompt = "a woman in a Buenos Aires apartment - moody lighting, 35mm film, cinematic, no text"
    c = _clip(visual_prompt=prompt)
    assert c.visual_prompt == prompt


def test_clip_visual_prompt_mostly_non_ascii_rejected() -> None:
    non_ascii_heavy = "n" * 80 + "a" * 20
    # Patch to use actual non-ASCII:
    non_ascii_heavy = "ñ" * 80 + "a" * 20
    assert len(non_ascii_heavy) == 100
    with pytest.raises(ValidationError) as exc_info:
        _clip(visual_prompt=non_ascii_heavy)
    assert "ASCII printable" in str(exc_info.value)


def test_clip_visual_prompt_too_short_rejected() -> None:
    with pytest.raises(ValidationError):
        _clip(visual_prompt="short")


def test_clip_narration_too_short_rejected() -> None:
    with pytest.raises(ValidationError):
        Clip(
            idx=1,
            narration="x" * 9,  # min_length=10
            visual_prompt=_GOOD_VISUAL,
            mood="tense",
            tts_text=_GOOD_TTS,
        )


def test_clip_narration_too_long_is_truncated() -> None:
    clip = Clip(
        idx=1,
        narration="x" * 501,
        visual_prompt=_GOOD_VISUAL,
        mood="tense",
        tts_text=_GOOD_TTS,
    )
    assert len(clip.narration) <= 500


def test_clip_invalid_mood_rejected() -> None:
    with pytest.raises(ValidationError):
        Clip(
            idx=1,
            narration=_GOOD_NARRATION,
            visual_prompt=_GOOD_VISUAL,
            mood="furious",  # type: ignore[arg-type]
            tts_text=_GOOD_TTS,
        )


def test_clip_all_moods_accepted() -> None:
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
        c = Clip(
            idx=1,
            narration=_GOOD_NARRATION,
            visual_prompt=_GOOD_VISUAL,
            mood=mood,  # type: ignore[arg-type]
            tts_text=_GOOD_TTS,
        )
        assert c.mood == mood


# ---------------------------------------------------------------------------
# ScriptwriterResponse tests
# ---------------------------------------------------------------------------


def test_response_valid_4_clips() -> None:
    r = _response_4()
    assert len(r.clips) == 4
    assert [c.idx for c in r.clips] == [1, 2, 3, 4]


def test_response_valid_6_clips() -> None:
    r = ScriptwriterResponse(
        title=_GOOD_TITLE,
        synopsis=_GOOD_SYNOPSIS,
        clips=[_clip(i) for i in range(1, 7)],
        cliffhanger=_GOOD_CLIFF,
        next_cliffhanger_seed=_GOOD_SEED,
    )
    assert len(r.clips) == 6


def test_response_non_contiguous_clips_rejected() -> None:
    # idx 1, 2, 4, 5 -- skips 3
    with pytest.raises(ValidationError) as exc_info:
        ScriptwriterResponse(
            title=_GOOD_TITLE,
            synopsis=_GOOD_SYNOPSIS,
            clips=[_clip(1), _clip(2), _clip(4), _clip(5)],
            cliffhanger=_GOOD_CLIFF,
            next_cliffhanger_seed=_GOOD_SEED,
        )
    assert "contiguous" in str(exc_info.value)


def test_response_duplicate_idx_rejected() -> None:
    with pytest.raises(ValidationError):
        ScriptwriterResponse(
            title=_GOOD_TITLE,
            synopsis=_GOOD_SYNOPSIS,
            clips=[_clip(1), _clip(1), _clip(2), _clip(3)],
            cliffhanger=_GOOD_CLIFF,
            next_cliffhanger_seed=_GOOD_SEED,
        )


def test_response_too_few_clips_rejected() -> None:
    with pytest.raises(ValidationError):
        ScriptwriterResponse(
            title=_GOOD_TITLE,
            synopsis=_GOOD_SYNOPSIS,
            clips=[_clip(1), _clip(2), _clip(3)],  # min_length=4
            cliffhanger=_GOOD_CLIFF,
            next_cliffhanger_seed=_GOOD_SEED,
        )


def test_response_too_many_clips_rejected() -> None:
    with pytest.raises(ValidationError):
        ScriptwriterResponse(
            title=_GOOD_TITLE,
            synopsis=_GOOD_SYNOPSIS,
            clips=[_clip(i) for i in range(1, 8)],  # 7 clips, max_length=6
            cliffhanger=_GOOD_CLIFF,
            next_cliffhanger_seed=_GOOD_SEED,
        )


def test_response_title_too_short_rejected() -> None:
    with pytest.raises(ValidationError):
        ScriptwriterResponse(
            title="Hi",  # min_length=5
            synopsis=_GOOD_SYNOPSIS,
            clips=[_clip(i) for i in range(1, 5)],
            cliffhanger=_GOOD_CLIFF,
            next_cliffhanger_seed=_GOOD_SEED,
        )


def test_response_synopsis_too_short_rejected() -> None:
    with pytest.raises(ValidationError):
        ScriptwriterResponse(
            title=_GOOD_TITLE,
            synopsis="Corto.",  # min_length=20
            clips=[_clip(i) for i in range(1, 5)],
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
