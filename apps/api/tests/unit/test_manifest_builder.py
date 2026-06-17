"""Unit tests: manifest_builder — build_comic (v1.0) and build_video (v2.0).

Module 008 / Task T-005 delta.

All tests are pure Python — no database, no I/O.

Coverage — build_comic (v1.0 / comic_panels):
  - SCHEMA_VERSION constant is "1.0".
  - manifest_kind field is "comic_panels".
  - winner_metadata_dict: clear winner, tiebreak, auto-continue.
  - correct top-level keys and schema_version.
  - panels serialized without provider_used.
  - winner mode populates winner_twist_id as string UUID.
  - auto-continue mode has all-null winner_metadata.
  - tiebreak -> runner_up_twist_id populated.
  - generation_metadata shape, degraded_reasons list.
  - manifest_kind in generation_metadata.
  - result is JSON-serializable.
  - build_manifest alias calls build_comic.

Coverage — build_video (v2.0 / video_mp4):
  - SCHEMA_VERSION_VIDEO constant is "2.0".
  - manifest_kind field is "video_mp4".
  - video_url and video_duration_s present.
  - clips serialized with expected keys.
  - no panel-only fields present.
  - generation_metadata has clip_provider_breakdown and ffmpeg_stitch.
  - result is JSON-serializable.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from app.domain.manifest_builder import (
    SCHEMA_VERSION,
    SCHEMA_VERSION_VIDEO,
    GenerationMetadata,
    ManifestClip,
    ManifestPanel,
    VideoGenerationMetadata,
    build_comic,
    build_manifest,
    build_video,
    winner_metadata_dict,
)
from app.domain.scriptwriter_response import Clip, ScriptwriterResponse
from app.domain.scriptwriter_response_v1 import Panel
from app.domain.scriptwriter_response_v1 import ScriptwriterResponse as ScriptwriterResponseV1
from app.domain.winner_selector import WinnerPick

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TWIST_PUBLIC_ID = uuid4()
_RUNNER_UP_ID = uuid4()

_GOOD_VISUAL = (
    "a woman reaches into a fractured antique mirror, cinematic, 35mm, no text"
)
_GOOD_NARRATION = "El espejo crujio como hielo viejo."
_GOOD_TTS = "El espejo crujio como hielo viejo."


def _script_v1(n_panels: int = 3) -> ScriptwriterResponseV1:
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
    return ScriptwriterResponseV1(
        title="Lo que habia detras del espejo",
        synopsis="Mariana acepta la propuesta del reflejo y descubre su otra-yo.",
        panels=panels,
        cliffhanger="Entonces escucho la voz de su madre.",
        next_cliffhanger_seed="La madre del 1998 alterno esta viva.",
    )


def _script_v2(n_clips: int = 4) -> ScriptwriterResponse:
    clips = [
        Clip(
            idx=i,
            narration=_GOOD_NARRATION,
            visual_prompt=_GOOD_VISUAL,
            mood="tense",
            tts_text=_GOOD_TTS,
        )
        for i in range(1, n_clips + 1)
    ]
    return ScriptwriterResponse(
        title="Lo que habia detras del espejo",
        synopsis="Mariana acepta la propuesta del reflejo y descubre su otra-yo.",
        clips=clips,
        cliffhanger="Entonces escucho la voz de su madre.",
        next_cliffhanger_seed="La madre del 1998 alterno esta viva.",
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


def _manifest_clips(n: int = 4) -> list[ManifestClip]:
    return [
        ManifestClip(
            idx=i,
            clip_url=f"https://r2.example/clips/{i}-ab12cd34.mp4",
            duration_s=5.0,
            narration=_GOOD_NARRATION,
            mood="tense",
            provider_used="hf",
            ok=True,
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


def _video_gen_meta(
    *, degraded: bool = False, reasons: list[str] | None = None
) -> VideoGenerationMetadata:
    return VideoGenerationMetadata(
        scriptwriter_model="gemini-2.0-flash",
        scriptwriter_provider="gemini",
        clip_provider_breakdown={"hf": 4},
        tts_provider="edge-tts",
        ffmpeg_stitch=True,
        started_at="2026-06-17T02:00:00Z",
        finished_at="2026-06-17T02:38:44Z",
        duration_ms=2324000,
        degraded=degraded,
        degraded_reasons=reasons or [],
    )


def _build_comic(**kwargs) -> dict:  # type: ignore[type-arg]
    return build_comic(
        script=kwargs.get("script", _script_v1()),
        panels=kwargs.get("panels", _manifest_panels()),
        winner=kwargs.get("winner", _winner_pick()),
        gen_meta=kwargs.get("gen_meta", _gen_meta()),
    )


def _build_video(**kwargs) -> dict:  # type: ignore[type-arg]
    return build_video(
        script=kwargs.get("script", _script_v2()),
        clips=kwargs.get("clips", _manifest_clips()),
        video_url=kwargs.get("video_url", "https://r2.example/chapter-ab12cd34.mp4"),
        video_duration_s=kwargs.get("video_duration_s", 22.5),
        winner=kwargs.get("winner", _winner_pick()),
        gen_meta=kwargs.get("gen_meta", _video_gen_meta()),
    )


# ---------------------------------------------------------------------------
# SCHEMA_VERSION constants
# ---------------------------------------------------------------------------


def test_schema_version_constant() -> None:
    assert SCHEMA_VERSION == "1.0"


def test_schema_version_video_constant() -> None:
    assert SCHEMA_VERSION_VIDEO == "2.0"


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
# build_comic — top-level shape
# ---------------------------------------------------------------------------


def test_build_comic_schema_version() -> None:
    assert _build_comic()["schema_version"] == "1.0"


def test_build_comic_manifest_kind() -> None:
    assert _build_comic()["manifest_kind"] == "comic_panels"


def test_build_comic_required_top_level_keys() -> None:
    m = _build_comic()
    assert set(m.keys()) == {
        "schema_version",
        "manifest_kind",
        "panels",
        "cliffhanger",
        "next_cliffhanger_seed",
        "winner_metadata",
        "generation_metadata",
    }


def test_build_comic_cliffhanger_from_script() -> None:
    m = _build_comic()
    assert m["cliffhanger"] == "Entonces escucho la voz de su madre."


def test_build_comic_next_cliffhanger_seed_from_script() -> None:
    m = _build_comic()
    assert m["next_cliffhanger_seed"] == "La madre del 1998 alterno esta viva."


# ---------------------------------------------------------------------------
# build_comic — panels
# ---------------------------------------------------------------------------


def test_build_comic_panel_count() -> None:
    m = _build_comic(panels=_manifest_panels(3))
    assert len(m["panels"]) == 3


def test_build_comic_panel_keys() -> None:
    panel = _build_comic()["panels"][0]
    assert set(panel.keys()) == {
        "idx", "image_url", "image_blurhash", "tts_url", "narration", "mood"
    }


def test_build_comic_panel_no_provider_used() -> None:
    for p in _build_comic()["panels"]:
        assert "provider_used" not in p


def test_build_comic_panel_image_url() -> None:
    m = _build_comic()
    assert m["panels"][0]["image_url"] == "https://r2.example/panel-1.webp"


def test_build_comic_panel_tts_url_nullable() -> None:
    panels = [
        ManifestPanel(
            idx=1,
            image_url="https://r2.example/panel-1.webp",
            image_blurhash=None,
            tts_url=None,
            narration=_GOOD_NARRATION,
            mood="tense",
            provider_used="fake",
        ),
        *_manifest_panels(2)[1:],
    ]
    m = build_comic(
        script=_script_v1(3),
        panels=panels,
        winner=_winner_pick(),
        gen_meta=_gen_meta(),
    )
    assert m["panels"][0]["tts_url"] is None


# ---------------------------------------------------------------------------
# build_comic — generation_metadata
# ---------------------------------------------------------------------------


def test_build_comic_generation_metadata_has_manifest_kind() -> None:
    gm = _build_comic()["generation_metadata"]
    assert gm["manifest_kind"] == "comic_panels"


def test_build_comic_generation_metadata_shape() -> None:
    gm = _build_comic()["generation_metadata"]
    assert gm["scriptwriter_model"] == "gemini-2.0-flash"
    assert gm["scriptwriter_provider"] == "gemini"
    assert gm["tts_provider"] == "edge-tts"
    assert gm["degraded"] is False
    assert gm["degraded_reasons"] == []


def test_build_comic_degraded_chapter() -> None:
    m = build_comic(
        script=_script_v1(),
        panels=_manifest_panels(),
        winner=_winner_pick(),
        gen_meta=_gen_meta(degraded=True, reasons=["panel_3_render_failed"]),
    )
    gm = m["generation_metadata"]
    assert gm["degraded"] is True
    assert "panel_3_render_failed" in gm["degraded_reasons"]


def test_build_comic_panel_provider_breakdown_is_dict() -> None:
    gm = _build_comic()["generation_metadata"]
    assert isinstance(gm["panel_provider_breakdown"], dict)
    assert gm["panel_provider_breakdown"]["pollinations"] == 2


# ---------------------------------------------------------------------------
# build_manifest alias
# ---------------------------------------------------------------------------


def test_build_manifest_alias_calls_build_comic() -> None:
    m = build_manifest(
        script=_script_v1(),
        panels=_manifest_panels(),
        winner=_winner_pick(),
        gen_meta=_gen_meta(),
    )
    assert m["schema_version"] == "1.0"
    assert m["manifest_kind"] == "comic_panels"


# ---------------------------------------------------------------------------
# build_comic — winner_metadata
# ---------------------------------------------------------------------------


def test_build_comic_winner_metadata_present() -> None:
    wm = _build_comic()["winner_metadata"]
    assert wm["winner_twist_id"] == str(_TWIST_PUBLIC_ID)
    assert wm["winner_author_display_name"] == "Alice"


def test_build_comic_auto_continue_winner_metadata_all_null() -> None:
    m = build_comic(
        script=_script_v1(),
        panels=_manifest_panels(),
        winner=_null_pick(),
        gen_meta=_gen_meta(),
    )
    wm = m["winner_metadata"]
    assert wm["winner_twist_id"] is None
    assert wm["winner_author_display_name"] is None
    assert wm["vote_count"] == 0


def test_build_comic_tiebreak_runner_up_populated() -> None:
    m = build_comic(
        script=_script_v1(),
        panels=_manifest_panels(),
        winner=_winner_pick(tiebreak=True, runner_up=_RUNNER_UP_ID),
        gen_meta=_gen_meta(),
    )
    wm = m["winner_metadata"]
    assert wm["tiebreak"] is True
    assert wm["runner_up_twist_id"] == str(_RUNNER_UP_ID)


# ---------------------------------------------------------------------------
# build_comic — JSON-serializability
# ---------------------------------------------------------------------------


def test_build_comic_is_json_serializable() -> None:
    m = _build_comic()
    dumped = json.dumps(m)
    assert json.loads(dumped)["schema_version"] == "1.0"


def test_build_comic_tiebreak_json_serializable() -> None:
    m = build_comic(
        script=_script_v1(),
        panels=_manifest_panels(),
        winner=_winner_pick(tiebreak=True, runner_up=_RUNNER_UP_ID),
        gen_meta=_gen_meta(degraded=True, reasons=["deadline_exceeded"]),
    )
    dumped = json.dumps(m)
    parsed = json.loads(dumped)
    assert parsed["winner_metadata"]["tiebreak"] is True


# ===========================================================================
# build_video — schema v2.0
# ===========================================================================


def test_build_video_schema_version() -> None:
    assert _build_video()["schema_version"] == "2.0"


def test_build_video_manifest_kind() -> None:
    assert _build_video()["manifest_kind"] == "video_mp4"


def test_build_video_required_top_level_keys() -> None:
    m = _build_video()
    assert set(m.keys()) == {
        "schema_version",
        "manifest_kind",
        "video_url",
        "video_duration_s",
        "clips",
        "cliffhanger",
        "next_cliffhanger_seed",
        "winner_metadata",
        "generation_metadata",
    }


def test_build_video_video_url() -> None:
    url = "https://r2.example/seasons/s01/abc/chapter-ab12cd34.mp4"
    m = _build_video(video_url=url)
    assert m["video_url"] == url


def test_build_video_duration() -> None:
    m = _build_video(video_duration_s=32.5)
    assert m["video_duration_s"] == 32.5


def test_build_video_clip_count() -> None:
    m = _build_video(clips=_manifest_clips(4))
    assert len(m["clips"]) == 4


def test_build_video_clip_keys() -> None:
    clip = _build_video()["clips"][0]
    assert set(clip.keys()) == {
        "idx", "clip_url", "duration_s", "narration", "mood", "provider", "ok"
    }


def test_build_video_clip_no_image_url() -> None:
    for c in _build_video()["clips"]:
        assert "image_url" not in c


def test_build_video_clip_url_format() -> None:
    m = _build_video()
    assert m["clips"][0]["clip_url"].endswith(".mp4")


def test_build_video_clip_ok_field() -> None:
    clips = [
        ManifestClip(idx=1, clip_url="https://r2.example/1.mp4", duration_s=5.0,
                     narration="x" * 10, mood="tense", provider_used="hf", ok=True),
        ManifestClip(idx=2, clip_url="https://r2.example/2.mp4", duration_s=5.0,
                     narration="x" * 10, mood="tense", provider_used="placeholder", ok=False),
        ManifestClip(idx=3, clip_url="https://r2.example/3.mp4", duration_s=5.0,
                     narration="x" * 10, mood="tense", provider_used="hf", ok=True),
        ManifestClip(idx=4, clip_url="https://r2.example/4.mp4", duration_s=5.0,
                     narration="x" * 10, mood="tense", provider_used="hf", ok=True),
    ]
    m = _build_video(clips=clips)
    assert m["clips"][1]["ok"] is False
    assert m["clips"][0]["ok"] is True


def test_build_video_generation_metadata_has_manifest_kind() -> None:
    gm = _build_video()["generation_metadata"]
    assert gm["manifest_kind"] == "video_mp4"


def test_build_video_generation_metadata_has_clip_provider_breakdown() -> None:
    gm = _build_video()["generation_metadata"]
    assert "clip_provider_breakdown" in gm
    assert isinstance(gm["clip_provider_breakdown"], dict)
    assert gm["clip_provider_breakdown"]["hf"] == 4


def test_build_video_generation_metadata_no_panel_breakdown() -> None:
    gm = _build_video()["generation_metadata"]
    assert "panel_provider_breakdown" not in gm


def test_build_video_generation_metadata_ffmpeg_stitch() -> None:
    gm = _build_video()["generation_metadata"]
    assert gm["ffmpeg_stitch"] is True


def test_build_video_cliffhanger_from_script() -> None:
    m = _build_video()
    assert m["cliffhanger"] == "Entonces escucho la voz de su madre."


def test_build_video_winner_metadata_present() -> None:
    wm = _build_video()["winner_metadata"]
    assert wm["winner_twist_id"] == str(_TWIST_PUBLIC_ID)


def test_build_video_auto_continue_all_null() -> None:
    m = _build_video(winner=_null_pick())
    wm = m["winner_metadata"]
    assert wm["winner_twist_id"] is None


def test_build_video_is_json_serializable() -> None:
    m = _build_video()
    dumped = json.dumps(m)
    assert json.loads(dumped)["schema_version"] == "2.0"


def test_build_video_degraded() -> None:
    m = _build_video(gen_meta=_video_gen_meta(degraded=True, reasons=["clip_2_placeholder"]))
    assert m["generation_metadata"]["degraded"] is True
    assert "clip_2_placeholder" in m["generation_metadata"]["degraded_reasons"]
