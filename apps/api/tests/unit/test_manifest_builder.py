"""Unit tests: manifest_builder.

Module 008 / Task T-005.

All tests are pure Python — no database, no I/O.

Coverage:
  - SCHEMA_VERSION constant is "1.0".
  - winner_metadata_dict: clear winner, tiebreak, auto-continue (null pick).
  - build_manifest: correct top-level keys and schema_version.
  - build_manifest: panels are serialized without provider_used.
  - build_manifest: winner mode populates winner_twist_id as string UUID.
  - build_manifest: auto-continue mode has all-null winner_metadata.
  - build_manifest: tiebreak → runner_up_twist_id populated.
  - build_manifest: generation_metadata shape, degraded_reasons list.
  - build_manifest: result is JSON-serializable (no UUID/datetime objects).
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from app.domain.manifest_builder import (
    SCHEMA_VERSION,
    GenerationMetadata,
    ManifestPanel,
    build_manifest,
    winner_metadata_dict,
)
from app.domain.scriptwriter_response import Panel, ScriptwriterResponse
from app.domain.winner_selector import WinnerPick

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TWIST_PUBLIC_ID = uuid4()
_RUNNER_UP_ID = uuid4()

_GOOD_VISUAL = (
    "a woman reaches into a fractured antique mirror, cinematic, 35mm, no text"
)
_GOOD_NARRATION = "El espejo crujió como hielo viejo."
_GOOD_TTS = "El espejo crujió como hielo viejo."


def _script(n_panels: int = 3) -> ScriptwriterResponse:
    panels = [
        Panel(
            idx=i,
            narration=_GOOD_NARRATION,
            visual_prompt=_GOOD_VISUAL,
            mood="tense",
            tts_text=_GOOD_TTS,
        )
        for i in range(1, n_panels + 1)
    ]
    return ScriptwriterResponse(
        title="Lo que había detrás del espejo",
        synopsis="Mariana acepta la propuesta del reflejo y descubre su otra-yo.",
        panels=panels,
        cliffhanger="Entonces escuchó la voz de su madre.",
        next_cliffhanger_seed="La madre del 1998 alterno está viva.",
    )


def _manifest_panels(n: int = 3) -> list[ManifestPanel]:
    return [
        ManifestPanel(
            idx=i,
            image_url=f"https://r2.example/panel-{i}.webp",
            image_blurhash="LKO2?V%2Tw=w]~RBVZRi",
            tts_url=f"https://r2.example/panel-{i}-tts.mp3",
            narration=_GOOD_NARRATION,
            mood="tense",
            provider_used="pollinations",
        )
        for i in range(1, n + 1)
    ]


def _winner_pick(
    *,
    twist_id: int = 1,
    public_id: UUID = _TWIST_PUBLIC_ID,
    display_name: str = "Alice",
    vote_count: int = 5,
    tiebreak: bool = False,
    runner_up: UUID | None = None,
) -> WinnerPick:
    return WinnerPick(
        winner_twist_id=twist_id,
        winner_public_id=public_id,
        winner_user_display_name=display_name,
        vote_count=vote_count,
        tiebreak=tiebreak,
        runner_up_twist_id=runner_up,
    )


def _null_pick() -> WinnerPick:
    return WinnerPick(
        winner_twist_id=None,
        winner_public_id=None,
        winner_user_display_name=None,
        vote_count=0,
        tiebreak=False,
        runner_up_twist_id=None,
    )


def _gen_meta(*, degraded: bool = False, reasons: list[str] | None = None) -> GenerationMetadata:
    return GenerationMetadata(
        scriptwriter_model="gemini-2.0-flash",
        scriptwriter_provider="gemini",
        panel_provider_breakdown={"pollinations": 2, "hf": 1},
        tts_provider="edge-tts",
        started_at="2026-06-14T02:00:00Z",
        finished_at="2026-06-14T02:30:00Z",
        duration_ms=1800000,
        degraded=degraded,
        degraded_reasons=reasons or [],
    )


def _build(**kwargs) -> dict:  # type: ignore[type-arg]
    return build_manifest(
        script=kwargs.get("script", _script()),
        panels=kwargs.get("panels", _manifest_panels()),
        winner=kwargs.get("winner", _winner_pick()),
        gen_meta=kwargs.get("gen_meta", _gen_meta()),
    )


# ---------------------------------------------------------------------------
# SCHEMA_VERSION
# ---------------------------------------------------------------------------


def test_schema_version_constant() -> None:
    assert SCHEMA_VERSION == "1.0"


# ---------------------------------------------------------------------------
# winner_metadata_dict
# ---------------------------------------------------------------------------


def test_winner_metadata_dict_clear_winner() -> None:
    d = winner_metadata_dict(_winner_pick(vote_count=12))
    assert d["winner_twist_id"] == str(_TWIST_PUBLIC_ID)
    assert d["winner_author_display_name"] == "Alice"
    assert d["vote_count"] == 12
    assert d["tiebreak"] is False
    assert d["runner_up_twist_id"] is None


def test_winner_metadata_dict_tiebreak() -> None:
    d = winner_metadata_dict(
        _winner_pick(tiebreak=True, runner_up=_RUNNER_UP_ID)
    )
    assert d["tiebreak"] is True
    assert d["runner_up_twist_id"] == str(_RUNNER_UP_ID)


def test_winner_metadata_dict_auto_continue() -> None:
    d = winner_metadata_dict(_null_pick())
    assert d["winner_twist_id"] is None
    assert d["winner_author_display_name"] is None
    assert d["vote_count"] == 0
    assert d["tiebreak"] is False
    assert d["runner_up_twist_id"] is None


def test_winner_metadata_dict_uuid_is_string() -> None:
    d = winner_metadata_dict(_winner_pick())
    assert isinstance(d["winner_twist_id"], str)


# ---------------------------------------------------------------------------
# build_manifest — top-level shape
# ---------------------------------------------------------------------------


def test_build_manifest_schema_version() -> None:
    assert _build()["schema_version"] == "1.0"


def test_build_manifest_required_top_level_keys() -> None:
    m = _build()
    assert set(m.keys()) == {
        "schema_version",
        "panels",
        "cliffhanger",
        "next_cliffhanger_seed",
        "winner_metadata",
        "generation_metadata",
    }


def test_build_manifest_cliffhanger_from_script() -> None:
    m = _build()
    assert m["cliffhanger"] == "Entonces escuchó la voz de su madre."


def test_build_manifest_next_cliffhanger_seed_from_script() -> None:
    m = _build()
    assert m["next_cliffhanger_seed"] == "La madre del 1998 alterno está viva."


# ---------------------------------------------------------------------------
# build_manifest — panels
# ---------------------------------------------------------------------------


def test_build_manifest_panel_count() -> None:
    m = _build(panels=_manifest_panels(3))
    assert len(m["panels"]) == 3


def test_build_manifest_panel_keys() -> None:
    panel = _build()["panels"][0]
    assert set(panel.keys()) == {
        "idx", "image_url", "image_blurhash", "tts_url", "narration", "mood"
    }


def test_build_manifest_panel_no_provider_used() -> None:
    # provider_used is pipeline-internal; MUST NOT appear in the manifest
    for p in _build()["panels"]:
        assert "provider_used" not in p


def test_build_manifest_panel_image_url() -> None:
    m = _build()
    assert m["panels"][0]["image_url"] == "https://r2.example/panel-1.webp"


def test_build_manifest_panel_tts_url_nullable() -> None:
    panels = [
        ManifestPanel(
            idx=1,
            image_url="https://r2.example/panel-1.webp",
            image_blurhash=None,
            tts_url=None,  # TTS disabled
            narration=_GOOD_NARRATION,
            mood="tense",
            provider_used="fake",
        ),
        *_manifest_panels(2)[1:],  # pad with panels 2 and 3
    ]
    m = build_manifest(
        script=_script(3),
        panels=panels,
        winner=_winner_pick(),
        gen_meta=_gen_meta(),
    )
    assert m["panels"][0]["tts_url"] is None


# ---------------------------------------------------------------------------
# build_manifest — winner_metadata
# ---------------------------------------------------------------------------


def test_build_manifest_winner_metadata_present() -> None:
    wm = _build()["winner_metadata"]
    assert wm["winner_twist_id"] == str(_TWIST_PUBLIC_ID)
    assert wm["winner_author_display_name"] == "Alice"


def test_build_manifest_auto_continue_winner_metadata_all_null() -> None:
    m = build_manifest(
        script=_script(),
        panels=_manifest_panels(),
        winner=_null_pick(),
        gen_meta=_gen_meta(),
    )
    wm = m["winner_metadata"]
    assert wm["winner_twist_id"] is None
    assert wm["winner_author_display_name"] is None
    assert wm["vote_count"] == 0


def test_build_manifest_tiebreak_runner_up_populated() -> None:
    m = build_manifest(
        script=_script(),
        panels=_manifest_panels(),
        winner=_winner_pick(tiebreak=True, runner_up=_RUNNER_UP_ID),
        gen_meta=_gen_meta(),
    )
    wm = m["winner_metadata"]
    assert wm["tiebreak"] is True
    assert wm["runner_up_twist_id"] == str(_RUNNER_UP_ID)


# ---------------------------------------------------------------------------
# build_manifest — generation_metadata
# ---------------------------------------------------------------------------


def test_build_manifest_generation_metadata_shape() -> None:
    gm = _build()["generation_metadata"]
    assert gm["scriptwriter_model"] == "gemini-2.0-flash"
    assert gm["scriptwriter_provider"] == "gemini"
    assert gm["tts_provider"] == "edge-tts"
    assert gm["degraded"] is False
    assert gm["degraded_reasons"] == []


def test_build_manifest_degraded_chapter() -> None:
    m = build_manifest(
        script=_script(),
        panels=_manifest_panels(),
        winner=_winner_pick(),
        gen_meta=_gen_meta(degraded=True, reasons=["panel_3_render_failed"]),
    )
    gm = m["generation_metadata"]
    assert gm["degraded"] is True
    assert "panel_3_render_failed" in gm["degraded_reasons"]


def test_build_manifest_panel_provider_breakdown_is_dict() -> None:
    gm = _build()["generation_metadata"]
    assert isinstance(gm["panel_provider_breakdown"], dict)
    assert gm["panel_provider_breakdown"]["pollinations"] == 2


# ---------------------------------------------------------------------------
# JSON-serializability
# ---------------------------------------------------------------------------


def test_build_manifest_is_json_serializable() -> None:
    m = _build()
    dumped = json.dumps(m)
    assert json.loads(dumped)["schema_version"] == "1.0"


def test_build_manifest_tiebreak_json_serializable() -> None:
    m = build_manifest(
        script=_script(),
        panels=_manifest_panels(),
        winner=_winner_pick(tiebreak=True, runner_up=_RUNNER_UP_ID),
        gen_meta=_gen_meta(degraded=True, reasons=["deadline_exceeded"]),
    )
    dumped = json.dumps(m)
    parsed = json.loads(dumped)
    assert parsed["winner_metadata"]["tiebreak"] is True
