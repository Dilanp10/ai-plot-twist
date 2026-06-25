"""Generation pipeline coordinator — end-to-end chapter generation.

Module 008 / Task T-010 (delta-video applied).

Orchestrates the nightly generation run for ONE chapter. Two paths:

  **T2V primary** (when ``video_router`` is provided and
  ``video_pipeline_enabled``):
    1. Pick the winner twist.
    2. Draft script (clips schema, v2.0).
    3. Render 4-6 video clips in parallel.
    4. Stitch clips + audio into a single chapter mp4 via ffmpeg.
    5. Build a v2.0 ``video_mp4`` manifest.

  **T2I fallback** (no video router, video disabled, or T2V failed):
    1. Pick the winner twist.
    2. Draft script.
    3. Render 3-4 comic panels in parallel via the image router.
    4. Build a v1.0 ``comic_panels`` manifest.

  Either path ends by persisting the new ``chapters`` row inside a
  single transaction and transitioning the cycle to ``PENDING_RELEASE``.

Failures trigger fallback:

- ``AllClipsFailedError`` — every clip's T2V failed → switch to T2I.
- ``StitchError`` — ffmpeg refused the clips → switch to T2I.
- Any ``video_pipeline_enabled=False`` or ``video_router=None`` —
  skip T2V outright.

Testability seams:

- :func:`_load_ctx_from_db` — patches avoid real DB reads.
- :func:`_persist_new_chapter` — patches avoid real DB writes.
- :func:`_transition_to_pending_release` — patches avoid executor logic.
- :func:`_run_panels` and :func:`_run_clips` — can be patched to inject
  controlled failures.
- :func:`render_clip` and :func:`stitch_clips` — imported at module level
  so the test suite can patch them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.clip_pipeline import (
    AllClipsFailedError,
    ClipResult,
    I2VBodyResult,
    render_clip,
    run_i2v,
)
from app.domain.intro_overlay import IntroRenderError, render_intro
from app.domain.manifest_builder import (
    GenerationMetadata,
    ManifestClip,
    ManifestPanel,
    VideoGenerationMetadata,
    build_comic,
    build_video,
)
from app.domain.panel_pipeline import PanelResult, render_panel
from app.domain.scriptwriter import Scriptwriter
from app.domain.scriptwriter_prompts import ChapterBrief, ScriptContext, SeasonBrief
from app.domain.scriptwriter_response import Clip, ScriptwriterResponse
from app.domain.scriptwriter_response_v1 import Panel as _PanelV1
from app.domain.scriptwriter_response_v3 import ScriptwriterResponseV3
from app.domain.stitch_pipeline import (
    StitchError,
    StitchLayerAResult,
    StitchResult,
    stitch_clips,
    stitch_layer_a,
)
from app.domain.winner_selector import WinnerPick, pick_winner
from app.infra.r2_uploader import R2Uploader
from app.providers.i2v.router import ImageToVideoProviderRouter
from app.providers.image import ImageProviderRouter
from app.providers.video import VideoProviderRouter

logger = logging.getLogger(__name__)

RECENT_CHAPTERS_LIMIT = 3
_T2I_FALLBACK_MAX_PANELS = 4  # clips 5-6 dropped per delta-video.md FR-018

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerationSummary:
    """Outcome of a single :func:`run_generation_pipeline` execution.

    ``panels_ok`` / ``panels_degraded`` carry dual meaning to keep the
    rerun-endpoint contract stable:
      - T2I path: literal panel counts.
      - T2V path: clip ok / degraded counts (each clip plays the role
        of one panel in the success-rate metric).
    """

    new_chapter_id: int
    new_chapter_public_id: UUID
    status: str
    panels_ok: int
    panels_degraded: int
    duration_ms: int
    has_winner: bool
    manifest_kind: str = "comic_panels"


@dataclass(frozen=True)
class _PipelineCtx:
    """All DB-derived state needed by the pipeline after context loading."""

    cycle_id: int
    season_id: int
    season_slug: str
    script_context: ScriptContext
    winner_pick: WinnerPick
    current_day_index: int
    new_chapter_public_id: UUID
    # R2 key of the winner character photo (None if no character set or
    # winner twist has no character_id). Used by Layer A (I2V).
    winner_character_r2_key: str | None = None


# ---------------------------------------------------------------------------
# DB helpers (internal — patchable in tests)
# ---------------------------------------------------------------------------


def _coerce_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        loaded = json.loads(value)
        if isinstance(loaded, dict):
            return loaded
    return {}


async def _load_ctx_from_db(
    session: AsyncSession,
    chapter_id: int,
) -> _PipelineCtx:
    """Load all DB context needed by the pipeline from a single session."""
    # --- Current chapter + season ---
    current_row = (
        await session.execute(
            sa.text(
                "SELECT c.id, c.season_id, c.day_index, c.title, c.synopsis, "
                "       c.manifest_json->>'cliffhanger' AS cliffhanger, "
                "       s.slug AS season_slug, s.title AS season_title, "
                "       s.bible_json "
                "FROM chapters c "
                "JOIN seasons s ON s.id = c.season_id "
                "WHERE c.id = :cid"
            ),
            {"cid": chapter_id},
        )
    ).mappings().one()

    season_id = int(current_row["season_id"])
    current_day_index = int(current_row["day_index"])
    bible = _coerce_json(current_row["bible_json"])

    # --- Recent chapters (cliffhanger from manifest_json) ---
    recent_rows = (
        await session.execute(
            sa.text(
                "SELECT day_index, title, synopsis, "
                "       manifest_json->>'cliffhanger' AS cliffhanger "
                "FROM chapters "
                "WHERE season_id = :sid AND day_index < :di "
                "ORDER BY day_index DESC "
                "LIMIT :lim"
            ),
            {"sid": season_id, "di": current_day_index, "lim": RECENT_CHAPTERS_LIMIT},
        )
    ).mappings().all()

    recent_chapters = [
        ChapterBrief(
            day_index=int(r["day_index"]),
            title=str(r["title"]),
            synopsis=str(r["synopsis"]),
            cliffhanger=str(r["cliffhanger"] or ""),
        )
        for r in reversed(recent_rows)
    ]

    current_chapter = ChapterBrief(
        day_index=current_day_index,
        title=str(current_row["title"]),
        synopsis=str(current_row["synopsis"]),
        cliffhanger=str(current_row["cliffhanger"] or ""),
    )

    season_brief = SeasonBrief(
        title=str(current_row["season_title"]),
        bible_json=bible,
    )

    # --- Winner selection ---
    winner_pick = await pick_winner(session, chapter_id)

    winner_content: str | None = None
    winner_character_r2_key: str | None = None
    if winner_pick.winner_twist_id is not None:
        twist_row = (
            await session.execute(
                sa.text(
                    "SELECT t.content, c.photo_r2_key AS char_photo "
                    "FROM twists t "
                    "LEFT JOIN characters c ON c.id = t.character_id "
                    "WHERE t.id = :tid"
                ),
                {"tid": winner_pick.winner_twist_id},
            )
        ).mappings().one_or_none()
        if twist_row is not None:
            winner_content = str(twist_row["content"])
            if twist_row["char_photo"] is not None:
                winner_character_r2_key = str(twist_row["char_photo"])

    # --- Cycle id ---
    cycle_row = (
        await session.execute(
            sa.text(
                "SELECT id FROM cycles "
                "WHERE chapter_id = :cid "
                "ORDER BY cycle_date DESC "
                "LIMIT 1"
            ),
            {"cid": chapter_id},
        )
    ).mappings().one()
    cycle_id = int(cycle_row["id"])

    return _PipelineCtx(
        cycle_id=cycle_id,
        season_id=season_id,
        season_slug=str(current_row["season_slug"]),
        script_context=ScriptContext(
            season=season_brief,
            recent_chapters=recent_chapters,
            current_chapter=current_chapter,
            next_day_index=current_day_index + 1,
            winner_content=winner_content,
        ),
        winner_pick=winner_pick,
        current_day_index=current_day_index,
        new_chapter_public_id=uuid4(),
        winner_character_r2_key=winner_character_r2_key,
    )


async def _persist_new_chapter(
    session: AsyncSession,
    *,
    cycle_id: int,
    season_id: int,
    next_day_index: int,
    new_chapter_public_id: UUID,
    title: str,
    synopsis: str,
    manifest: dict[str, Any],
    status: str,
) -> int:
    """Insert a new chapter row and update the cycle's next_chapter_id."""
    result = await session.execute(
        sa.text(
            "INSERT INTO chapters "
            "  (public_id, season_id, day_index, title, synopsis, "
            "   manifest_json, status) "
            "VALUES "
            "  (:public_id, :season_id, :day_index, :title, :synopsis, "
            "   cast(:manifest_json AS jsonb), :status) "
            "RETURNING id"
        ),
        {
            "public_id": str(new_chapter_public_id),
            "season_id": season_id,
            "day_index": next_day_index,
            "title": title,
            "synopsis": synopsis,
            "manifest_json": json.dumps(manifest),
            "status": status,
        },
    )
    new_chapter_id = int(result.mappings().one()["id"])

    await session.execute(
        sa.text(
            "UPDATE cycles SET next_chapter_id = :ncid WHERE id = :cid"
        ),
        {"ncid": new_chapter_id, "cid": cycle_id},
    )
    return new_chapter_id


async def _transition_to_pending_release(
    session: AsyncSession,
    cycle_id: int,
    new_chapter_id: int,
) -> None:
    """Transition the cycle to PENDING_RELEASE after persisting the chapter."""
    trigger_id = f"generation-{cycle_id}-{uuid4()}"
    await session.execute(
        sa.text(
            "INSERT INTO state_transitions "
            "  (cycle_id, from_state, to_state, triggered_by, trigger_id, next_chapter_id) "
            "VALUES "
            "  (:cycle_id, "
            "   (SELECT state FROM cycles WHERE id = :cycle_id), "
            "   'PENDING_RELEASE', 'side_effect', :trigger_id, :ncid) "
            "ON CONFLICT (cycle_id, to_state, trigger_id) "
            "WHERE trigger_id IS NOT NULL DO NOTHING"
        ),
        {
            "cycle_id": cycle_id,
            "trigger_id": trigger_id,
            "ncid": new_chapter_id,
        },
    )
    await session.execute(
        sa.text(
            "UPDATE cycles "
            "SET state = 'PENDING_RELEASE', "
            "    state_entered_at = now() "
            "WHERE id = :cid"
        ),
        {"cid": cycle_id},
    )


# ---------------------------------------------------------------------------
# Clip rendering (T2V primary path)
# ---------------------------------------------------------------------------


async def _run_clips(
    script: ScriptwriterResponse,
    *,
    chapter_id: int,
    chapter_public_id: UUID,
    season_slug: str,
    video_router: VideoProviderRouter,
    uploader: R2Uploader,
    tts_voice: str,
    placeholder_video_url: str,
    placeholder_video_bytes: bytes,
    clip_concurrency: int,
    clip_duration_s: float,
    timeout_s: float,
    tmp_dir: Path,
) -> list[ClipResult]:
    """Render every clip in parallel.

    Raises
    ------
    AllClipsFailedError
        Every clip ended up as a placeholder (``ok=False``).
    """
    sem = asyncio.Semaphore(clip_concurrency)
    tracker: dict[int, ClipResult] = {}

    async def _render_one(clip: Clip) -> ClipResult:
        async with sem:
            result = await render_clip(
                clip=clip,
                chapter_id=chapter_id,
                chapter_public_id=chapter_public_id,
                season_slug=season_slug,
                video_router=video_router,
                uploader=uploader,
                tts_voice=tts_voice,
                placeholder_video_url=placeholder_video_url,
                placeholder_bytes=placeholder_video_bytes,
                tmp_dir=tmp_dir,
                duration_s=clip_duration_s,
            )
        tracker[clip.idx] = result
        return result

    coros = [_render_one(c) for c in script.clips]

    try:
        raw = await asyncio.wait_for(
            asyncio.gather(*coros, return_exceptions=True),
            timeout=max(timeout_s, 0.0),
        )
        results: list[ClipResult] = []
        for clip, entry in zip(script.clips, raw, strict=True):
            if isinstance(entry, BaseException):
                logger.warning(
                    "clip_%d_gather_exception %s: %s",
                    clip.idx,
                    type(entry).__name__,
                    entry,
                )
                clip_tmp = tmp_dir / f"clip_{clip.idx}.mp4"
                clip_tmp.write_bytes(placeholder_video_bytes)
                results.append(
                    ClipResult(
                        idx=clip.idx,
                        clip_url=placeholder_video_url,
                        clip_path=str(clip_tmp),
                        tts_path=None,
                        duration_s=0.0,
                        provider_used="placeholder",
                        ok=False,
                    )
                )
            else:
                results.append(entry)
    except (TimeoutError, asyncio.CancelledError) as exc:
        logger.warning(
            "clip_rendering_deadline_exceeded clips_completed=%d/%d",
            len(tracker),
            len(script.clips),
        )
        results = []
        for clip in script.clips:
            if clip.idx in tracker:
                results.append(tracker[clip.idx])
            else:
                clip_tmp = tmp_dir / f"clip_{clip.idx}.mp4"
                clip_tmp.write_bytes(placeholder_video_bytes)
                results.append(
                    ClipResult(
                        idx=clip.idx,
                        clip_url=placeholder_video_url,
                        clip_path=str(clip_tmp),
                        tts_path=None,
                        duration_s=0.0,
                        provider_used="placeholder",
                        ok=False,
                    )
                )
        if isinstance(exc, asyncio.CancelledError):
            raise

    if all(not r.ok for r in results):
        raise AllClipsFailedError(
            f"all {len(results)} clips fell back to placeholder"
        )

    return results


# ---------------------------------------------------------------------------
# Panel rendering (T2I fallback path)
# ---------------------------------------------------------------------------


async def _run_panels(
    script: ScriptwriterResponse,
    *,
    chapter_id: int,
    chapter_public_id: UUID,
    season_slug: str,
    image_router: ImageProviderRouter,
    uploader: R2Uploader,
    tts_voice: str,
    placeholder_url: str,
    panel_concurrency: int,
    timeout_s: float,
) -> list[PanelResult]:
    """Render the T2I fallback panels.

    Uses up to :data:`_T2I_FALLBACK_MAX_PANELS` clips from the script as
    panels (Clip and Panel share an identical field layout). Clips beyond
    the cap are dropped — the T2I path is intentionally lower-resolution
    storytelling.
    """
    used_clips = list(script.clips)[:_T2I_FALLBACK_MAX_PANELS]

    tracker: dict[int, PanelResult] = {}
    sem = asyncio.Semaphore(panel_concurrency)

    async def _render_one(clip: Clip) -> PanelResult:
        async with sem:
            result = await render_panel(
                panel=cast(_PanelV1, clip),
                chapter_id=chapter_id,
                chapter_public_id=chapter_public_id,
                season_slug=season_slug,
                image_router=image_router,
                uploader=uploader,
                tts_voice=tts_voice,
                placeholder_url=placeholder_url,
            )
        tracker[clip.idx] = result
        return result

    coros = [_render_one(c) for c in used_clips]

    try:
        raw = await asyncio.wait_for(
            asyncio.gather(*coros, return_exceptions=True),
            timeout=max(timeout_s, 0.0),
        )
        results: list[PanelResult] = []
        for clip, entry in zip(used_clips, raw, strict=True):
            if isinstance(entry, BaseException):
                logger.warning(
                    "panel_%d_gather_exception %s: %s",
                    clip.idx,
                    type(entry).__name__,
                    entry,
                )
                results.append(
                    PanelResult(
                        idx=clip.idx,
                        image_url=placeholder_url,
                        image_blurhash=None,
                        tts_url=None,
                        provider_used="placeholder",
                        ok=False,
                    )
                )
            else:
                results.append(entry)
        return results

    except (TimeoutError, asyncio.CancelledError) as exc:
        logger.warning(
            "panel_rendering_deadline_exceeded panels_completed=%d/%d",
            len(tracker),
            len(used_clips),
        )
        results = []
        for clip in used_clips:
            if clip.idx in tracker:
                results.append(tracker[clip.idx])
            else:
                results.append(
                    PanelResult(
                        idx=clip.idx,
                        image_url=placeholder_url,
                        image_blurhash=None,
                        tts_url=None,
                        provider_used="placeholder",
                        ok=False,
                    )
                )
        if isinstance(exc, asyncio.CancelledError):
            raise
        return results


# ---------------------------------------------------------------------------
# Manifest assembly
# ---------------------------------------------------------------------------


def _build_video_manifest(
    *,
    script: ScriptwriterResponse,
    clips: list[ClipResult],
    stitch: StitchResult,
    winner: WinnerPick,
    started_at: str,
    finished_at: str,
    duration_ms: int,
) -> tuple[dict[str, Any], str]:
    """Return ``(manifest_dict, status)`` for the T2V success path."""
    degraded_reasons: list[str] = []
    manifest_clips: list[ManifestClip] = []
    for cr, c in zip(clips, script.clips, strict=True):
        if not cr.ok:
            degraded_reasons.append(f"clip_{cr.idx}_placeholder")
        manifest_clips.append(
            ManifestClip(
                idx=cr.idx,
                clip_url=cr.clip_url,
                duration_s=cr.duration_s,
                narration=c.narration,
                mood=c.mood,
                provider_used=cr.provider_used,
                ok=cr.ok,
            )
        )

    status = "ready" if not degraded_reasons else "ready_degraded"

    breakdown: dict[str, int] = {}
    for cr in clips:
        breakdown[cr.provider_used] = breakdown.get(cr.provider_used, 0) + 1

    gen_meta = VideoGenerationMetadata(
        scriptwriter_model="unknown",
        scriptwriter_provider="unknown",
        clip_provider_breakdown=breakdown,
        tts_provider="edge-tts" if any(c.tts_path for c in clips) else None,
        ffmpeg_stitch=True,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        degraded=bool(degraded_reasons),
        degraded_reasons=degraded_reasons,
    )

    manifest = build_video(
        script=script,
        clips=manifest_clips,
        video_url=stitch.video_url,
        video_duration_s=stitch.video_duration_s,
        winner=winner,
        gen_meta=gen_meta,
    )
    return manifest, status


def _build_layer_a_manifest(
    *,
    script_v3: ScriptwriterResponseV3,
    i2v_body: I2VBodyResult,
    stitch: StitchLayerAResult,
    winner: WinnerPick,
    started_at: str,
    finished_at: str,
    duration_ms: int,
) -> tuple[dict[str, Any], str]:
    """Return ``(manifest_dict, status)`` for the Layer A (I2V) path."""
    gen_meta = {
        "scriptwriter_model": "unknown",
        "scriptwriter_provider": "unknown",
        "i2v_provider": i2v_body.provider_used,
        "ffmpeg_stitch": True,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration_ms,
        "degraded": False,
        "degraded_reasons": [],
    }
    manifest: dict[str, Any] = {
        "schema_version": "3.0",
        "manifest_kind": "video_i2v",
        "title": script_v3.title,
        "synopsis": script_v3.synopsis,
        "cliffhanger": script_v3.cliffhanger,
        "video_url": stitch.video_url,
        "video_duration_s": stitch.video_duration_s,
        "scene": {
            "visual_prompt": script_v3.scene.visual_prompt,
            "narration": script_v3.scene.narration,
            "mood": script_v3.scene.mood,
            "provider_used": i2v_body.provider_used,
        },
        "tts_url": i2v_body.tts_path,
        "winner_metadata": {
            "twist_public_id": str(winner.winner_public_id),
            "vote_count": winner.vote_count,
            "tiebreak": winner.tiebreak,
        },
        "generation_metadata": gen_meta,
    }
    return manifest, "ready"


def _build_comic_manifest(
    *,
    script: ScriptwriterResponse,
    panel_results: list[PanelResult],
    winner: WinnerPick,
    started_at: str,
    finished_at: str,
    duration_ms: int,
    fallback_reason: str | None,
) -> tuple[dict[str, Any], str]:
    """Return ``(manifest_dict, status)`` for the T2I path / fallback.

    ``fallback_reason`` is appended to ``degraded_reasons`` so ops can
    distinguish T2V failures from a chapter that ran the T2I path natively.
    """
    used_clips = list(script.clips)[:_T2I_FALLBACK_MAX_PANELS]

    degraded_reasons: list[str] = []
    if fallback_reason is not None:
        degraded_reasons.append(fallback_reason)

    manifest_panels: list[ManifestPanel] = []
    for pr, c in zip(panel_results, used_clips, strict=True):
        if not pr.ok:
            degraded_reasons.append(f"panel_{pr.idx}_placeholder")
        manifest_panels.append(
            ManifestPanel(
                idx=pr.idx,
                image_url=pr.image_url,
                image_blurhash=pr.image_blurhash,
                tts_url=pr.tts_url,
                narration=c.narration,
                mood=c.mood,
                provider_used=pr.provider_used,
            )
        )

    panels_ok = sum(1 for pr in panel_results if pr.ok)
    panels_degraded = len(panel_results) - panels_ok
    status = "ready" if panels_degraded == 0 and fallback_reason is None else "ready_degraded"

    gen_meta = GenerationMetadata(
        scriptwriter_model="unknown",
        scriptwriter_provider="unknown",
        panel_provider_breakdown={pr.provider_used: 1 for pr in panel_results},
        tts_provider="edge-tts" if any(pr.tts_url for pr in panel_results) else None,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        degraded=bool(degraded_reasons),
        degraded_reasons=degraded_reasons,
    )

    manifest = build_comic(
        script=script,
        panels=manifest_panels,
        winner=winner,
        gen_meta=gen_meta,
    )
    return manifest, status


# ---------------------------------------------------------------------------
# Main coordinator
# ---------------------------------------------------------------------------


async def run_generation_pipeline(
    chapter_id: int,
    *,
    session: AsyncSession,
    scriptwriter: Scriptwriter,
    image_router: ImageProviderRouter,
    uploader: R2Uploader,
    placeholder_url: str,
    tts_voice: str,
    panel_concurrency: int,
    deadline_s: float,
    video_router: VideoProviderRouter | None = None,
    placeholder_video_url: str | None = None,
    placeholder_video_bytes: bytes | None = None,
    clip_concurrency: int = 4,
    clip_duration_s: float = 5.0,
    video_pipeline_enabled: bool = True,
    skip_cycle_transition: bool = False,
    # Layer A (I2V) — Delta 008
    i2v_router: ImageToVideoProviderRouter | None = None,
    intro_bg_path: Path | None = None,
    outro_path: Path | None = None,
    r2_public_base_url: str | None = None,
    intro_duration_s: float = 2.0,
    outro_duration_s: float = 2.0,
    intro_font_size: int = 64,
    intro_font_color: str = "white",
) -> GenerationSummary:
    """Run the full generation pipeline for *chapter_id*.

    See module docstring for the orchestration order.

    Layer A (I2V) → Layer B (T2V) → Layer C (T2I) fallback chain.

    Layer A requires *i2v_router*, *intro_bg_path*, *outro_path*, and a
    winner with a *character_id*.  When any of those is missing the pipeline
    skips Layer A.

    Layer B (T2V) requires *video_router*, *placeholder_video_url*, and
    *placeholder_video_bytes*.  When any of those is missing the pipeline
    runs Layer C (T2I) directly.
    """
    pipeline_start = time.monotonic()
    started_at_iso = datetime.now(UTC).isoformat()

    logger.info("generation_started chapter_id=%d", chapter_id)

    ctx = await _load_ctx_from_db(session, chapter_id)
    winner_pick = ctx.winner_pick
    has_winner = winner_pick.winner_twist_id is not None

    logger.info(
        "winner_picked chapter_id=%d twist_id=%s vote_count=%d tiebreak=%s",
        chapter_id,
        winner_pick.winner_public_id,
        winner_pick.vote_count,
        winner_pick.tiebreak,
    )

    # Resolve winner character image URL (needed for Layer A)
    winner_character_image_url: str | None = None
    if ctx.winner_character_r2_key and r2_public_base_url:
        base = r2_public_base_url.rstrip("/")
        key = ctx.winner_character_r2_key.lstrip("/")
        winner_character_image_url = f"{base}/{key}"

    # Draft v2 script (used by Layer B and Layer C)
    script = await scriptwriter.draft(ctx.script_context)

    logger.info(
        "scriptwriter_done chapter_id=%d clips=%d",
        chapter_id,
        len(script.clips),
    )

    can_run_i2v = (
        i2v_router is not None
        and intro_bg_path is not None
        and intro_bg_path.exists()
        and outro_path is not None
        and outro_path.exists()
        and winner_character_image_url is not None
    )
    can_run_t2v = (
        video_pipeline_enabled
        and video_router is not None
        and placeholder_video_url is not None
        and placeholder_video_bytes is not None
    )

    manifest: dict[str, Any] | None = None
    status: str | None = None
    summary_ok = 0
    summary_degraded = 0
    manifest_kind = "comic_panels"
    chapter_title = script.title
    chapter_synopsis = script.synopsis
    fallback_reason: str | None = (
        None if (can_run_i2v or can_run_t2v) else "video_pipeline_disabled"
    )

    # -------------------------------------------------------------------------
    # Layer A (I2V) — 14-second composition (Delta 008)
    # -------------------------------------------------------------------------
    if can_run_i2v:
        assert i2v_router is not None
        assert intro_bg_path is not None
        assert outro_path is not None
        assert winner_character_image_url is not None

        tmp_dir_a = Path(tempfile.mkdtemp(prefix=f"chapter-{chapter_id}-a-"))
        try:
            script_v3 = await scriptwriter.draft_v3(ctx.script_context)
            chapter_title = script_v3.title
            chapter_synopsis = script_v3.synopsis

            i2v_body = await run_i2v(
                scene=script_v3.scene,
                chapter_id=chapter_id,
                image_url=winner_character_image_url,
                i2v_router=i2v_router,
                uploader=uploader,
                tts_voice=tts_voice,
                placeholder_bytes=placeholder_video_bytes or b"\x00" * 8,
                tmp_dir=tmp_dir_a,
            )

            intro_mp4 = tmp_dir_a / "intro.mp4"
            await render_intro(
                bg_path=intro_bg_path,
                out_path=intro_mp4,
                text=script_v3.cliffhanger,
                duration_s=intro_duration_s,
                font_size=intro_font_size,
                font_color=intro_font_color,
            )

            outro_tmp = tmp_dir_a / "outro.mp4"
            shutil.copy(str(outro_path), str(outro_tmp))

            stitch_a = await stitch_layer_a(
                intro_mp4=intro_mp4,
                body_mp4=i2v_body.body_mp4,
                outro_mp4=outro_tmp,
                tmp_dir=tmp_dir_a,
                uploader=uploader,
                season_slug=ctx.season_slug,
                chapter_public_id=ctx.new_chapter_public_id,
            )

            finished_at_iso = datetime.now(UTC).isoformat()
            duration_ms = int((time.monotonic() - pipeline_start) * 1000)
            manifest, status = _build_layer_a_manifest(
                script_v3=script_v3,
                i2v_body=i2v_body,
                stitch=stitch_a,
                winner=winner_pick,
                started_at=started_at_iso,
                finished_at=finished_at_iso,
                duration_ms=duration_ms,
            )
            manifest_kind = "video_i2v"
            summary_ok = 1
            summary_degraded = 0
            logger.info(
                "chapter_render_layer layer=A chapter_id=%d provider=%s",
                chapter_id,
                i2v_body.provider_used,
            )
        except (IntroRenderError, StitchError, Exception) as exc:
            fallback_reason = "layer_a_failed"
            logger.warning(
                "chapter_render_layer_fallback from=A reason=%s chapter_id=%d error=%s",
                fallback_reason,
                chapter_id,
                exc,
            )
        finally:
            shutil.rmtree(tmp_dir_a, ignore_errors=True)

    # -------------------------------------------------------------------------
    # Layer B (T2V) primary path
    # -------------------------------------------------------------------------
    if manifest is None and can_run_t2v:
        assert video_router is not None
        assert placeholder_video_url is not None
        assert placeholder_video_bytes is not None

        tmp_dir = Path(tempfile.mkdtemp(prefix=f"chapter-{chapter_id}-"))
        try:
            elapsed = time.monotonic() - pipeline_start
            clip_timeout = max(deadline_s - elapsed, 0.0)

            try:
                clip_results = await _run_clips(
                    script,
                    chapter_id=chapter_id,
                    chapter_public_id=ctx.new_chapter_public_id,
                    season_slug=ctx.season_slug,
                    video_router=video_router,
                    uploader=uploader,
                    tts_voice=tts_voice,
                    placeholder_video_url=placeholder_video_url,
                    placeholder_video_bytes=placeholder_video_bytes,
                    clip_concurrency=clip_concurrency,
                    clip_duration_s=clip_duration_s,
                    timeout_s=clip_timeout,
                    tmp_dir=tmp_dir,
                )
                stitch_result = await stitch_clips(
                    clips=clip_results,
                    tmp_dir=tmp_dir,
                    uploader=uploader,
                    season_slug=ctx.season_slug,
                    chapter_public_id=ctx.new_chapter_public_id,
                    clip_duration_s=clip_duration_s,
                )
            except AllClipsFailedError:
                fallback_reason = "all_clips_failed"
                logger.warning(
                    "generation_t2i_fallback reason=%s chapter_id=%d",
                    fallback_reason,
                    chapter_id,
                )
            except StitchError as exc:
                fallback_reason = "stitch_failed"
                logger.warning(
                    "generation_t2i_fallback reason=%s chapter_id=%d error=%s",
                    fallback_reason,
                    chapter_id,
                    exc,
                )
            else:
                finished_at_iso = datetime.now(UTC).isoformat()
                duration_ms = int((time.monotonic() - pipeline_start) * 1000)
                manifest, status = _build_video_manifest(
                    script=script,
                    clips=clip_results,
                    stitch=stitch_result,
                    winner=winner_pick,
                    started_at=started_at_iso,
                    finished_at=finished_at_iso,
                    duration_ms=duration_ms,
                )
                manifest_kind = "video_mp4"
                summary_ok = sum(1 for c in clip_results if c.ok)
                summary_degraded = len(clip_results) - summary_ok
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # T2I path (native or fallback)
    # -------------------------------------------------------------------------
    if manifest is None:
        elapsed = time.monotonic() - pipeline_start
        panel_timeout = max(deadline_s - elapsed, 0.0)

        panel_results = await _run_panels(
            script,
            chapter_id=chapter_id,
            chapter_public_id=ctx.new_chapter_public_id,
            season_slug=ctx.season_slug,
            image_router=image_router,
            uploader=uploader,
            tts_voice=tts_voice,
            placeholder_url=placeholder_url,
            panel_concurrency=panel_concurrency,
            timeout_s=panel_timeout,
        )

        finished_at_iso = datetime.now(UTC).isoformat()
        duration_ms = int((time.monotonic() - pipeline_start) * 1000)

        # When the coordinator was never asked to run any video path, this is
        # a *native* T2I run, not a fallback — clear the synthetic reason so
        # ops doesn't see false alarms.
        native_t2i = fallback_reason == "video_pipeline_disabled"
        manifest, status = _build_comic_manifest(
            script=script,
            panel_results=panel_results,
            winner=winner_pick,
            started_at=started_at_iso,
            finished_at=finished_at_iso,
            duration_ms=duration_ms,
            fallback_reason=None if native_t2i else fallback_reason,
        )
        manifest_kind = "comic_panels"
        summary_ok = sum(1 for pr in panel_results if pr.ok)
        summary_degraded = len(panel_results) - summary_ok

    assert manifest is not None
    assert status is not None

    # --- Persist chapter + update cycle ---
    new_chapter_id = await _persist_new_chapter(
        session,
        cycle_id=ctx.cycle_id,
        season_id=ctx.season_id,
        next_day_index=ctx.script_context.next_day_index,
        new_chapter_public_id=ctx.new_chapter_public_id,
        title=chapter_title,
        synopsis=chapter_synopsis,
        manifest=manifest,
        status=status,
    )
    await session.commit()

    # --- Transition cycle to PENDING_RELEASE ---
    if not skip_cycle_transition:
        await _transition_to_pending_release(session, ctx.cycle_id, new_chapter_id)
        await session.commit()

    duration_ms_final = int((time.monotonic() - pipeline_start) * 1000)

    logger.info(
        "generation_completed chapter_id=%d new_chapter_id=%d "
        "status=%s manifest_kind=%s duration_ms=%d ok=%d degraded=%d "
        "has_winner=%s skipped_cycle_transition=%s",
        chapter_id,
        new_chapter_id,
        status,
        manifest_kind,
        duration_ms_final,
        summary_ok,
        summary_degraded,
        has_winner,
        skip_cycle_transition,
    )

    return GenerationSummary(
        new_chapter_id=new_chapter_id,
        new_chapter_public_id=ctx.new_chapter_public_id,
        status=status,
        panels_ok=summary_ok,
        panels_degraded=summary_degraded,
        duration_ms=duration_ms_final,
        has_winner=has_winner,
        manifest_kind=manifest_kind,
    )


# ---------------------------------------------------------------------------
# Side-effect factory (T-011 wires this into the side_effects registry)
# ---------------------------------------------------------------------------


def build_generation_pipeline_side_effect(
    session_factory: async_sessionmaker[AsyncSession],
    scriptwriter: Scriptwriter,
    image_router: ImageProviderRouter,
    uploader: R2Uploader,
    *,
    placeholder_url: str,
    tts_voice: str,
    panel_concurrency: int,
    deadline_s: float,
    video_router: VideoProviderRouter | None = None,
    placeholder_video_url: str | None = None,
    placeholder_video_bytes: bytes | None = None,
    clip_concurrency: int = 4,
    clip_duration_s: float = 5.0,
    video_pipeline_enabled: bool = True,
    # Layer A (I2V) — Delta 008
    i2v_router: ImageToVideoProviderRouter | None = None,
    intro_bg_path: Path | None = None,
    outro_path: Path | None = None,
    r2_public_base_url: str | None = None,
    intro_duration_s: float = 2.0,
    outro_duration_s: float = 2.0,
    intro_font_size: int = 64,
    intro_font_color: str = "white",
) -> Callable[[int], Awaitable[None]]:
    """Return a ``generation_pipeline`` side-effect bound to its dependencies.

    Optional video-path args mirror :func:`run_generation_pipeline`. When
    *video_router* / *placeholder_video_url* / *placeholder_video_bytes*
    are absent (or *video_pipeline_enabled* is False) the closure runs
    the T2I path directly.

    Layer A (I2V) args are also optional; when absent the coordinator skips
    directly to Layer B (T2V) or Layer C (T2I).
    """

    async def _generation_pipeline(chapter_id: int) -> None:
        async with session_factory() as session:
            await run_generation_pipeline(
                chapter_id,
                session=session,
                scriptwriter=scriptwriter,
                image_router=image_router,
                uploader=uploader,
                placeholder_url=placeholder_url,
                tts_voice=tts_voice,
                panel_concurrency=panel_concurrency,
                deadline_s=deadline_s,
                video_router=video_router,
                placeholder_video_url=placeholder_video_url,
                placeholder_video_bytes=placeholder_video_bytes,
                clip_concurrency=clip_concurrency,
                clip_duration_s=clip_duration_s,
                video_pipeline_enabled=video_pipeline_enabled,
                i2v_router=i2v_router,
                intro_bg_path=intro_bg_path,
                outro_path=outro_path,
                r2_public_base_url=r2_public_base_url,
                intro_duration_s=intro_duration_s,
                outro_duration_s=outro_duration_s,
                intro_font_size=intro_font_size,
                intro_font_color=intro_font_color,
            )

    return _generation_pipeline
