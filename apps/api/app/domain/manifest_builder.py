"""Manifest builder — pure functions that assemble ``chapters.manifest_json``.

Module 008 / Task T-005 delta.

Two builder functions, one per output path:

``build_comic``  (schema_version 1.0, manifest_kind "comic_panels")
    T2I fallback path — panels rendered by ImageProviderRouter.
    Shape: panels[], cliffhanger, winner_metadata, generation_metadata.

``build_video``  (schema_version 2.0, manifest_kind "video_mp4")
    T2V primary path — clips rendered by VideoProviderRouter + ffmpeg stitch.
    Shape: video_url, video_duration_s, clips[], cliffhanger, winner_metadata,
           generation_metadata.

All functions are pure: no DB calls, no I/O, no side-effects.
The coordinator (T-010) calls exactly one builder after the pipeline settles.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.scriptwriter_response import ScriptwriterResponse
from app.domain.scriptwriter_response_v1 import ScriptwriterResponse as ScriptwriterResponseV1
from app.domain.winner_selector import WinnerPick

__all__ = [
    "SCHEMA_VERSION",
    "SCHEMA_VERSION_VIDEO",
    "GenerationMetadata",
    "ManifestClip",
    "ManifestPanel",
    "VideoGenerationMetadata",
    "build_comic",
    "build_manifest",  # backward-compat alias for build_comic
    "build_video",
    "winner_metadata_dict",
]

SCHEMA_VERSION = "1.0"
SCHEMA_VERSION_VIDEO = "2.0"


# ---------------------------------------------------------------------------
# Intermediate dataclasses — produced by the render pipelines
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManifestPanel:
    """Settled state of one panel after image render + TTS + R2 upload (T2I path)."""

    idx: int
    image_url: str
    image_blurhash: str | None
    tts_url: str | None
    narration: str
    mood: str
    provider_used: str


@dataclass(frozen=True)
class ManifestClip:
    """Settled state of one clip after video render + TTS + R2 upload (T2V path)."""

    idx: int
    clip_url: str
    duration_s: float
    narration: str
    mood: str
    provider_used: str
    ok: bool


# ---------------------------------------------------------------------------
# GenerationMetadata variants
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerationMetadata:
    """Ops record for the T2I comic path (schema v1.0)."""

    scriptwriter_model: str
    scriptwriter_provider: str
    panel_provider_breakdown: dict[str, int]
    tts_provider: str | None
    started_at: str
    finished_at: str
    duration_ms: int
    degraded: bool
    degraded_reasons: list[str]


@dataclass(frozen=True)
class VideoGenerationMetadata:
    """Ops record for the T2V video path (schema v2.0)."""

    scriptwriter_model: str
    scriptwriter_provider: str
    clip_provider_breakdown: dict[str, int]
    tts_provider: str | None
    ffmpeg_stitch: bool
    started_at: str
    finished_at: str
    duration_ms: int
    degraded: bool
    degraded_reasons: list[str]


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def winner_metadata_dict(pick: WinnerPick) -> dict[str, Any]:
    """Convert a :class:`WinnerPick` to the ``winner_metadata`` dict shape."""
    return {
        "winner_twist_id": (
            str(pick.winner_public_id) if pick.winner_public_id is not None else None
        ),
        "winner_author_display_name": pick.winner_user_display_name,
        "vote_count": pick.vote_count,
        "tiebreak": pick.tiebreak,
        "runner_up_twist_id": (
            str(pick.runner_up_twist_id)
            if pick.runner_up_twist_id is not None
            else None
        ),
    }


# ---------------------------------------------------------------------------
# build_comic — schema v1.0
# ---------------------------------------------------------------------------


def build_comic(
    *,
    script: ScriptwriterResponse | ScriptwriterResponseV1,
    panels: list[ManifestPanel],
    winner: WinnerPick,
    gen_meta: GenerationMetadata,
) -> dict[str, Any]:
    """Assemble ``manifest_json`` for the T2I comic path (schema_version 1.0).

    Parameters
    ----------
    script:
        Parsed scriptwriter output (v1 or v2 schema — only top-level narrative
        fields are used: cliffhanger, next_cliffhanger_seed).
    panels:
        Settled panel list from the panel pipeline.
    winner:
        Winner pick from :func:`~app.domain.winner_selector.pick_winner`.
    gen_meta:
        Coordinator-assembled generation metadata.
    """
    panel_dicts: list[dict[str, Any]] = [
        {
            "idx": p.idx,
            "image_url": p.image_url,
            "image_blurhash": p.image_blurhash,
            "tts_url": p.tts_url,
            "narration": p.narration,
            "mood": p.mood,
        }
        for p in panels
    ]

    gen_meta_dict: dict[str, Any] = {
        "manifest_kind": "comic_panels",
        "scriptwriter_model": gen_meta.scriptwriter_model,
        "scriptwriter_provider": gen_meta.scriptwriter_provider,
        "panel_provider_breakdown": dict(gen_meta.panel_provider_breakdown),
        "tts_provider": gen_meta.tts_provider,
        "started_at": gen_meta.started_at,
        "finished_at": gen_meta.finished_at,
        "duration_ms": gen_meta.duration_ms,
        "degraded": gen_meta.degraded,
        "degraded_reasons": list(gen_meta.degraded_reasons),
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_kind": "comic_panels",
        "panels": panel_dicts,
        "cliffhanger": script.cliffhanger,
        "next_cliffhanger_seed": script.next_cliffhanger_seed,
        "winner_metadata": winner_metadata_dict(winner),
        "generation_metadata": gen_meta_dict,
    }


# backward-compat alias — generation_pipeline.py calls build_manifest until T-010
build_manifest = build_comic


# ---------------------------------------------------------------------------
# build_video — schema v2.0
# ---------------------------------------------------------------------------


def build_video(
    *,
    script: ScriptwriterResponse,
    clips: list[ManifestClip],
    video_url: str,
    video_duration_s: float,
    winner: WinnerPick,
    gen_meta: VideoGenerationMetadata,
) -> dict[str, Any]:
    """Assemble ``manifest_json`` for the T2V video path (schema_version 2.0).

    Parameters
    ----------
    script:
        Parsed scriptwriter output (clips schema, v2.0).
    clips:
        Settled clip list from the clip pipeline.
    video_url:
        Public R2 URL of the stitched chapter `.mp4`.
    video_duration_s:
        Actual duration of the stitched video (from ffmpeg output).
    winner:
        Winner pick from :func:`~app.domain.winner_selector.pick_winner`.
    gen_meta:
        Coordinator-assembled video generation metadata.
    """
    clip_dicts: list[dict[str, Any]] = [
        {
            "idx": c.idx,
            "clip_url": c.clip_url,
            "duration_s": c.duration_s,
            "narration": c.narration,
            "mood": c.mood,
            "provider": c.provider_used,
            "ok": c.ok,
        }
        for c in clips
    ]

    gen_meta_dict: dict[str, Any] = {
        "manifest_kind": "video_mp4",
        "scriptwriter_model": gen_meta.scriptwriter_model,
        "scriptwriter_provider": gen_meta.scriptwriter_provider,
        "clip_provider_breakdown": dict(gen_meta.clip_provider_breakdown),
        "tts_provider": gen_meta.tts_provider,
        "ffmpeg_stitch": gen_meta.ffmpeg_stitch,
        "started_at": gen_meta.started_at,
        "finished_at": gen_meta.finished_at,
        "duration_ms": gen_meta.duration_ms,
        "degraded": gen_meta.degraded,
        "degraded_reasons": list(gen_meta.degraded_reasons),
    }

    return {
        "schema_version": SCHEMA_VERSION_VIDEO,
        "manifest_kind": "video_mp4",
        "video_url": video_url,
        "video_duration_s": video_duration_s,
        "clips": clip_dicts,
        "cliffhanger": script.cliffhanger,
        "next_cliffhanger_seed": script.next_cliffhanger_seed,
        "winner_metadata": winner_metadata_dict(winner),
        "generation_metadata": gen_meta_dict,
    }
