"""Shared helpers for generation pipeline integration tests.

Module 008 / Task T-010.

All tests patch the three DB seams so no real database is required:
  - ``_load_ctx_from_db``
  - ``_persist_new_chapter``
  - ``_transition_to_pending_release``

Real pipeline components are used for everything else:
  - FakeLLMProvider -> Scriptwriter
  - FakeImageProvider -> ImageProviderRouter
  - AsyncMock -> R2Uploader
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from app.domain.clip_pipeline import I2VBodyResult
from app.domain.generation_pipeline import _PipelineCtx
from app.domain.scriptwriter import Scriptwriter
from app.domain.scriptwriter_prompts import ChapterBrief, ScriptContext, SeasonBrief
from app.domain.scriptwriter_response import Clip, ScriptwriterResponse
from app.domain.scriptwriter_response_v3 import Scene, ScriptwriterResponseV3
from app.domain.stitch_pipeline import StitchLayerAResult
from app.domain.winner_selector import WinnerPick
from app.infra.r2_uploader import R2Uploader
from app.providers.i2v.fake import FakeImageToVideoProvider
from app.providers.i2v.router import ImageToVideoProviderRouter
from app.providers.image.fake import FakeImageProvider
from app.providers.image.router import ImageProviderRouter
from app.providers.llm.fake import FakeLLMProvider
from app.providers.llm.router import LLMProviderRouter
from app.providers.video import (
    MINIMAL_MP4,
    FakeVideoProvider,
    VideoProviderRouter,
)

# ---------------------------------------------------------------------------
# Fixed identifiers (stable across test runs for assertion equality)
# ---------------------------------------------------------------------------

CHAPTER_ID = 10
SEASON_ID = 1
SEASON_SLUG = "s01-el-tunel"
CYCLE_ID = 42
WINNER_CHARACTER_R2_KEY = "assets/characters/elena/photo.jpg"
R2_PUBLIC_BASE_URL = "https://r2.example.com"
NEW_CHAPTER_PUBLIC_ID: UUID = uuid4()
PLACEHOLDER_URL = "https://assets.example.com/static/placeholder.webp"
PLACEHOLDER_VIDEO_URL = "https://assets.example.com/static/placeholder.mp4"
PLACEHOLDER_VIDEO_BYTES = MINIMAL_MP4
TTS_VOICE = "es-AR-ElenaNeural"

_GOOD_VISUAL = "a shattered mirror reflecting two timelines, cinematic 35mm"
_GOOD_NARRATION = "El espejo crujió como hielo viejo al amanecer."
_GOOD_TTS_TEXT = "El espejo crujió como hielo viejo al amanecer."


# ---------------------------------------------------------------------------
# Preset objects
# ---------------------------------------------------------------------------


def make_winner_pick(
    *,
    with_winner: bool = True,
    tiebreak: bool = False,
) -> WinnerPick:
    if with_winner:
        return WinnerPick(
            winner_twist_id=99,
            winner_public_id=uuid4(),
            winner_user_display_name="Alice",
            vote_count=7,
            tiebreak=tiebreak,
            runner_up_twist_id=uuid4() if tiebreak else None,
        )
    return WinnerPick(
        winner_twist_id=None,
        winner_public_id=None,
        winner_user_display_name=None,
        vote_count=0,
        tiebreak=False,
        runner_up_twist_id=None,
    )


def make_ctx(
    *,
    winner_pick: WinnerPick | None = None,
    winner_content: str | None = "La propuesta ganadora de Alice.",
    n_clips: int = 4,
) -> _PipelineCtx:
    """Return a preset _PipelineCtx (no DB needed)."""
    if winner_pick is None:
        winner_pick = make_winner_pick(with_winner=(winner_content is not None))
    return _PipelineCtx(
        cycle_id=CYCLE_ID,
        season_id=SEASON_ID,
        season_slug=SEASON_SLUG,
        script_context=ScriptContext(
            season=SeasonBrief(
                title="El tunel sin fondo",
                bible_json={"genre": "thriller", "setting": "Buenos Aires 2026"},
            ),
            recent_chapters=[
                ChapterBrief(
                    day_index=9,
                    title="La decision",
                    synopsis="Valentina eligio cruzar el espejo.",
                    cliffhanger="Que la esperaba del otro lado?",
                )
            ],
            current_chapter=ChapterBrief(
                day_index=10,
                title="El otro lado",
                synopsis="Valentina descubrio que su doble la esperaba.",
                cliffhanger="La doble susurro su nombre al reves.",
            ),
            next_day_index=11,
            winner_content=winner_content,
        ),
        winner_pick=winner_pick,
        current_day_index=10,
        new_chapter_public_id=NEW_CHAPTER_PUBLIC_ID,
    )


def make_script(n_clips: int = 4) -> ScriptwriterResponse:
    clips = [
        Clip(
            idx=i,
            narration=_GOOD_NARRATION,
            visual_prompt=_GOOD_VISUAL,
            mood="tense",
            tts_text=_GOOD_TTS_TEXT,
        )
        for i in range(1, n_clips + 1)
    ]
    return ScriptwriterResponse(
        title="El capitulo once",
        synopsis="Valentina descifra el mensaje de su doble y escapa.",
        clips=clips,
        cliffhanger="Un tercer espejo aparecio donde no habia ninguno.",
        next_cliffhanger_seed="El tercer espejo muestra el futuro.",
    )


# ---------------------------------------------------------------------------
# Component factories
# ---------------------------------------------------------------------------


def make_scriptwriter(script: ScriptwriterResponse) -> Scriptwriter:
    """Return a Scriptwriter backed by a FakeLLMProvider seeded with *script*."""
    provider = FakeLLMProvider([script])
    router = LLMProviderRouter([provider], check_health=False)
    return Scriptwriter(router)


def make_image_router() -> ImageProviderRouter:
    """Return an ImageProviderRouter backed by FakeImageProvider."""
    return ImageProviderRouter(
        [FakeImageProvider()],
        check_health=False,
        backoff_schedule_seconds=(0.0,),
    )


def make_video_router(*, fail: bool = False) -> VideoProviderRouter:
    """Return a VideoProviderRouter backed by FakeVideoProvider (or empty chain)."""
    if fail:
        return VideoProviderRouter(providers=[], check_health=False)
    return VideoProviderRouter(
        providers=[FakeVideoProvider()],
        check_health=False,
        backoff_schedule_seconds=(0.0,),
    )


def make_uploader(
    base_url: str = "https://r2.example.com",
) -> R2Uploader:
    """Return a mock R2Uploader that returns a URL from the key."""
    uploader = MagicMock(spec=R2Uploader)
    uploader.upload = AsyncMock(
        side_effect=lambda key, body, ct: f"{base_url}/{key}"
    )
    return uploader


def make_ctx_i2v(
    *,
    winner_content: str | None = "La propuesta ganadora de Alice.",
) -> _PipelineCtx:
    """Return a _PipelineCtx with winner_character_r2_key set (Layer A capable)."""
    winner_pick = make_winner_pick(with_winner=(winner_content is not None))
    return _PipelineCtx(
        cycle_id=CYCLE_ID,
        season_id=SEASON_ID,
        season_slug=SEASON_SLUG,
        script_context=ScriptContext(
            season=SeasonBrief(
                title="El tunel sin fondo",
                bible_json={"genre": "thriller", "setting": "Buenos Aires 2026"},
            ),
            recent_chapters=[
                ChapterBrief(
                    day_index=9,
                    title="La decision",
                    synopsis="Valentina eligio cruzar el espejo.",
                    cliffhanger="Que la esperaba del otro lado?",
                )
            ],
            current_chapter=ChapterBrief(
                day_index=10,
                title="El otro lado",
                synopsis="Valentina descubrio que su doble la esperaba.",
                cliffhanger="La doble susurro su nombre al reves.",
            ),
            next_day_index=11,
            winner_content=winner_content,
        ),
        winner_pick=winner_pick,
        current_day_index=10,
        new_chapter_public_id=NEW_CHAPTER_PUBLIC_ID,
        winner_character_r2_key=WINNER_CHARACTER_R2_KEY,
    )


def make_script_v3() -> ScriptwriterResponseV3:
    return ScriptwriterResponseV3(
        title="El capitulo once",
        synopsis="Valentina descifra el mensaje de su doble y escapa.",
        cliffhanger="Un tercer espejo aparecio donde no habia ninguno.",
        scene=Scene(
            visual_prompt="a woman walks through a shattered mirror in slow motion, cinematic 35mm",
            narration="El espejo crujio como hielo viejo al amanecer.",
            mood="tense",
        ),
        next_cliffhanger_seed="El tercer espejo muestra el futuro.",
    )


def make_i2v_router() -> ImageToVideoProviderRouter:
    return ImageToVideoProviderRouter(
        providers=[FakeImageToVideoProvider()],
        check_health=False,
        backoff_schedule_seconds=(0.0,),
    )


def make_stub_i2v_body_result(tmp_path: Path) -> I2VBodyResult:
    body_mp4 = tmp_path / "body.mp4"
    body_mp4.write_bytes(b"FAKEBODY")
    return I2VBodyResult(
        body_mp4=body_mp4,
        duration_s=10.0,
        provider_used="fake_i2v",
        tts_path=None,
    )


def make_stub_stitch_layer_a_result() -> StitchLayerAResult:
    return StitchLayerAResult(
        video_url="https://r2.example.com/seasons/s01-el-tunel/chapter-ab12cd34.mp4",
        video_duration_s=14.0,
        video_bytes_len=8192,
    )


def make_mock_session() -> AsyncMock:
    """Return a minimal AsyncSession mock (commit is a no-op)."""
    session = AsyncMock()
    session.commit = AsyncMock()
    return session
