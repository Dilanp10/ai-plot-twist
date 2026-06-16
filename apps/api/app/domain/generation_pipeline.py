"""Generation pipeline coordinator — end-to-end chapter generation.

Module 008 / Task T-010.

Orchestrates the nightly generation run described in spec FR-001..FR-011:

  1. Load season, chapter, cycle context and pick the winner twist.
  2. Draft the script via :class:`Scriptwriter` (LLM-backed).
  3. Render every panel in parallel using :class:`ImageProviderRouter`.
  4. TTS + R2 upload per panel (handled by :func:`render_panel`).
  5. Build :func:`build_manifest` from the collected results.
  6. Persist the new ``chapters`` row and update
     ``cycles.next_chapter_id`` in one atomic transaction.
  7. Transition the cycle to ``PENDING_RELEASE``.

Deadline handling (FR-009, R-008): a ``deadline_s`` timeout wraps the
panel rendering phase via ``asyncio.wait_for``.  A per-panel tracker dict
captures results as panels complete, so panels that finished before the
deadline keep their real URLs; the rest fall back to ``placeholder_url``.

Testability seams:

- :func:`_load_ctx_from_db` — patches avoid real DB reads.
- :func:`_persist_new_chapter` — patches avoid real DB writes.
- :func:`_transition_to_pending_release` — patches avoid executor logic.
- :func:`_run_panels` — can be patched to inject controlled failures.

All internal helpers use the ``_`` prefix so they appear in the module
namespace and are easily patched with ``unittest.mock.patch``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.manifest_builder import (
    GenerationMetadata,
    ManifestPanel,
    build_manifest,
)
from app.domain.panel_pipeline import PanelResult, render_panel
from app.domain.scriptwriter import Scriptwriter
from app.domain.scriptwriter_prompts import ChapterBrief, ScriptContext, SeasonBrief
from app.domain.scriptwriter_response import Panel, ScriptwriterResponse
from app.domain.winner_selector import WinnerPick, pick_winner
from app.infra.r2_uploader import R2Uploader
from app.providers.image import ImageProviderRouter

logger = logging.getLogger(__name__)

RECENT_CHAPTERS_LIMIT = 3

# ---------------------------------------------------------------------------
# Internal context dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerationSummary:
    """Outcome of a single :func:`run_generation_pipeline` execution.

    Returned so the admin rerun endpoint can describe what changed
    without re-querying the DB. The cycle-driven side-effect path
    discards this value (its closure adapts the signature to
    ``Callable[[int], Awaitable[None]]``).
    """

    new_chapter_id: int
    new_chapter_public_id: UUID
    status: str
    panels_ok: int
    panels_degraded: int
    duration_ms: int
    has_winner: bool


@dataclass(frozen=True)
class _PipelineCtx:
    """All DB-derived state needed by the pipeline after context loading.

    Attributes
    ----------
    cycle_id:
        The active cycle's integer id (for the cycle transition).
    season_id:
        FK used when inserting the new chapter.
    season_slug:
        URL-safe season slug (used in R2 key paths).
    script_context:
        Ready-to-use :class:`ScriptContext` for the scriptwriter.
    winner_pick:
        Outcome of :func:`pick_winner` — may carry no winner
        (auto-continue mode) when ``winner_twist_id is None``.
    current_day_index:
        ``day_index`` of the chapter being generated from
        (used to derive ``next_day_index``).
    new_chapter_public_id:
        Pre-generated UUID for the new chapter row.  Used in R2 key
        paths BEFORE the DB INSERT, then inserted explicitly so the
        key paths remain valid after the chapter is persisted.
    """

    cycle_id: int
    season_id: int
    season_slug: str
    script_context: ScriptContext
    winner_pick: WinnerPick
    current_day_index: int
    new_chapter_public_id: UUID


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
    """Load all DB context needed by the pipeline from a single session.

    Steps:
    1. Load the current chapter + season (with bible_json + manifest_json).
    2. Load recent chapters (with their cliffhangers from manifest_json).
    3. Pick the winner twist via :func:`pick_winner`.
    4. If a winner exists, load its twist content.
    5. Load the cycle_id for the given chapter.
    6. Pre-generate the new chapter's public UUID.
    """
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
    if winner_pick.winner_twist_id is not None:
        twist_row = (
            await session.execute(
                sa.text(
                    "SELECT content FROM twists WHERE id = :tid"
                ),
                {"tid": winner_pick.winner_twist_id},
            )
        ).mappings().one_or_none()
        if twist_row is not None:
            winner_content = str(twist_row["content"])

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
    )


async def _persist_new_chapter(
    session: AsyncSession,
    *,
    cycle_id: int,
    season_id: int,
    next_day_index: int,
    new_chapter_public_id: UUID,
    script: ScriptwriterResponse,
    manifest: dict[str, Any],
    status: str,
) -> int:
    """Insert a new chapter row and update the cycle's next_chapter_id.

    Both writes happen inside a single transaction: the caller's *session*
    must be committed by the caller after this returns.

    Returns the new chapter's integer id.
    """
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
            "title": script.title,
            "synopsis": script.synopsis,
            "manifest_json": json.dumps(manifest),
            "status": status,
        },
    )
    new_chapter_id = int(result.mappings().one()["id"])

    await session.execute(
        sa.text(
            "UPDATE cycles "
            "SET next_chapter_id = :ncid "
            "WHERE id = :cid"
        ),
        {"ncid": new_chapter_id, "cid": cycle_id},
    )
    return new_chapter_id


async def _transition_to_pending_release(
    session: AsyncSession,
    cycle_id: int,
    new_chapter_id: int,
) -> None:
    """Transition the cycle to PENDING_RELEASE after persisting the chapter.

    Uses a direct UPDATE (not cycle_executor.transition) because:
    - The executor acquires an advisory lock and re-reads cycle state,
      but we are already inside the generation transaction.
    - The side-effect infrastructure already serialises concurrent calls.
    """
    trigger_id = f"generation-{cycle_id}-{uuid4()}"
    await session.execute(
        sa.text(
            "INSERT INTO state_transitions "
            "  (cycle_id, from_state, to_state, triggered_by, trigger_id, next_chapter_id) "
            "VALUES "
            "  (:cycle_id, "
            "   (SELECT state FROM cycles WHERE id = :cycle_id), "
            "   'PENDING_RELEASE', 'side_effect', :trigger_id, :ncid) "
            # Match the actual partial unique index on state_transitions
            # (cycle_id, to_state, trigger_id) WHERE trigger_id IS NOT NULL —
            # ON CONFLICT (trigger_id) alone has no matching constraint.
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
# Panel rendering with completion tracking (for deadline-safe partial results)
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
    """Render all panels with a deadline; completed panels are preserved.

    Panels that finish before *timeout_s* keep their real URLs.
    Panels still in flight when the timeout fires are cancelled and
    returned as placeholder ``PanelResult`` entries (``ok=False``).
    """
    tracker: dict[int, PanelResult] = {}
    sem = asyncio.Semaphore(panel_concurrency)

    async def _render_one(panel: Panel) -> PanelResult:
        async with sem:
            result = await render_panel(
                panel=panel,
                chapter_id=chapter_id,
                chapter_public_id=chapter_public_id,
                season_slug=season_slug,
                image_router=image_router,
                uploader=uploader,
                tts_voice=tts_voice,
                placeholder_url=placeholder_url,
            )
        tracker[panel.idx] = result
        return result

    coros = [_render_one(p) for p in script.panels]

    try:
        raw = await asyncio.wait_for(
            asyncio.gather(*coros, return_exceptions=True),
            timeout=max(timeout_s, 0.0),
        )
        # gather with return_exceptions=True: each entry is PanelResult or BaseException
        results: list[PanelResult] = []
        for panel, entry in zip(script.panels, raw, strict=True):
            if isinstance(entry, BaseException):
                logger.warning(
                    "panel_%d_gather_exception %s: %s",
                    panel.idx,
                    type(entry).__name__,
                    entry,
                )
                results.append(
                    PanelResult(
                        idx=panel.idx,
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
            len(script.panels),
        )
        # Build results: keep completed, placeholder for the rest
        results = []
        for panel in script.panels:
            if panel.idx in tracker:
                results.append(tracker[panel.idx])
            else:
                results.append(
                    PanelResult(
                        idx=panel.idx,
                        image_url=placeholder_url,
                        image_blurhash=None,
                        tts_url=None,
                        provider_used="placeholder",
                        ok=False,
                    )
                )
        if isinstance(exc, asyncio.CancelledError):
            raise  # propagate if the outer call was also cancelled
        return results


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
    skip_cycle_transition: bool = False,
) -> GenerationSummary:
    """Run the full generation pipeline for *chapter_id*.

    Parameters
    ----------
    chapter_id:
        The current chapter whose twist was voted on.  The pipeline
        generates the NEXT chapter.
    session:
        An open ``AsyncSession``.  This function commits the transaction.
    scriptwriter:
        Pre-wired :class:`Scriptwriter` (LLMProviderRouter inside).
    image_router:
        Pre-wired :class:`ImageProviderRouter`.
    uploader:
        Pre-wired :class:`R2Uploader`.
    placeholder_url:
        Public URL of the static placeholder image.
    tts_voice:
        edge-tts voice name (e.g. ``"es-AR-ElenaNeural"``).
    panel_concurrency:
        Max parallel ``render_panel`` calls (``asyncio.Semaphore``).
    deadline_s:
        Hard wall-clock budget for the panel phase.  Any panels not
        done within this limit fall back to placeholder.
    skip_cycle_transition:
        When ``True``, skip the final ``PENDING_RELEASE`` transition.
        Used by the admin rerun endpoint (FR-013): rerun replaces a
        manifest without touching cycle state.

    Returns
    -------
    GenerationSummary
        Counts and identifiers describing the persisted chapter.

    Raises
    ------
    LLMProviderError
        When the scriptwriter fails (all LLM providers exhausted).
        Module 003's ``safe_side_effect`` wrapper catches this and
        drives the cycle to ``FAILED``.
    """
    pipeline_start = time.monotonic()
    started_at_iso = datetime.now(UTC).isoformat()

    logger.info("generation_started chapter_id=%d", chapter_id)

    # --- Step 1: Load context + pick winner ---
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

    # --- Step 2: Draft script ---
    script = await scriptwriter.draft(ctx.script_context)

    logger.info(
        "scriptwriter_done chapter_id=%d panels=%d",
        chapter_id,
        len(script.panels),
    )

    # --- Step 3-4: Render panels + TTS (with deadline remaining) ---
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

    # --- Step 5: Build manifest ---
    panels_ok = sum(1 for pr in panel_results if pr.ok)
    panels_degraded = len(panel_results) - panels_ok
    status = "ready" if panels_degraded == 0 else "ready_degraded"

    manifest_panels: list[ManifestPanel] = []
    degraded_reasons: list[str] = []
    for pr, p in zip(panel_results, script.panels, strict=True):
        if not pr.ok:
            degraded_reasons.append(f"panel_{pr.idx}_placeholder")
        manifest_panels.append(
            ManifestPanel(
                idx=pr.idx,
                image_url=pr.image_url,
                image_blurhash=pr.image_blurhash,
                tts_url=pr.tts_url,
                narration=p.narration,
                mood=p.mood,
                provider_used=pr.provider_used,
            )
        )

    finished_at_iso = datetime.now(UTC).isoformat()
    duration_ms = int((time.monotonic() - pipeline_start) * 1000)

    gen_meta = GenerationMetadata(
        scriptwriter_model="unknown",
        scriptwriter_provider="unknown",
        panel_provider_breakdown={pr.provider_used: 1 for pr in panel_results},
        tts_provider="edge-tts" if any(pr.tts_url for pr in panel_results) else None,
        started_at=started_at_iso,
        finished_at=finished_at_iso,
        duration_ms=duration_ms,
        degraded=status == "ready_degraded",
        degraded_reasons=degraded_reasons,
    )

    manifest = build_manifest(
        script=script,
        panels=manifest_panels,
        winner=winner_pick,
        gen_meta=gen_meta,
    )

    # --- Step 6: Persist chapter + update cycle ---
    new_chapter_id = await _persist_new_chapter(
        session,
        cycle_id=ctx.cycle_id,
        season_id=ctx.season_id,
        next_day_index=ctx.script_context.next_day_index,
        new_chapter_public_id=ctx.new_chapter_public_id,
        script=script,
        manifest=manifest,
        status=status,
    )
    await session.commit()

    # --- Step 7: Transition cycle to PENDING_RELEASE ---
    if not skip_cycle_transition:
        await _transition_to_pending_release(session, ctx.cycle_id, new_chapter_id)
        await session.commit()

    logger.info(
        "generation_completed chapter_id=%d new_chapter_id=%d "
        "status=%s duration_ms=%d panels_ok=%d panels_degraded=%d "
        "has_winner=%s skipped_cycle_transition=%s",
        chapter_id,
        new_chapter_id,
        status,
        duration_ms,
        panels_ok,
        panels_degraded,
        has_winner,
        skip_cycle_transition,
    )

    return GenerationSummary(
        new_chapter_id=new_chapter_id,
        new_chapter_public_id=ctx.new_chapter_public_id,
        status=status,
        panels_ok=panels_ok,
        panels_degraded=panels_degraded,
        duration_ms=duration_ms,
        has_winner=has_winner,
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
) -> Callable[[int], Awaitable[None]]:
    """Return a ``generation_pipeline`` side-effect bound to its dependencies.

    The returned callable matches ``SideEffect = Callable[[int], Awaitable[None]]``
    expected by :mod:`app.domain.side_effects`.

    Exceptions propagate out of the closure so module 003's
    ``safe_side_effect`` wrapper can drive the cycle to ``FAILED``.
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
            )

    return _generation_pipeline
