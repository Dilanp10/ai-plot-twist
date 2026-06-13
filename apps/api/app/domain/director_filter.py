"""Director's filter orchestrator — LLM-driven moderation pipeline.

Module 006 / Task T-009.

Drives the end-to-end pipeline described in SDD §4.2 and FR-005..FR-011:

  1. Load the chapter's pending twists (or every non-deleted twist when
     replaying — see ``allow_already_classified``).
  2. Build the LLM context once (season bible, current chapter manifest,
     last three chapters).
  3. Chunk twists into batches of ``batch_size`` (default 25) and, per
     batch:
        a) Render the user prompt.
        b) Call :meth:`LLMProviderRouter.chat_json` asking for a
           :class:`DirectorBatchResponse`.
        c) Map verdicts back to twists; default-deny any missing
           ``twist_id`` (FR-009); apply the slur post-filter to
           ``approved`` verdicts (FR-010); truncate ``reason`` > 80
           chars.
        d) Persist via
           :meth:`TwistsRepo.update_status_bulk` and commit.

The cycle transition to ``VOTACION`` is intentionally NOT performed
here. T-010 wraps this function in a side-effect closure that:
  * adapts the signature to ``(chapter_id: int) -> None`` (FR-013);
  * resolves the ``cycle_id`` and dispatches
    ``cycle_executor.transition(to='VOTACION', triggered_by='side_effect')``
    after the run completes (FR-012).

Keeping transition out of T-009 means the same orchestrator powers the
admin replay endpoint (T-011), which must NOT change cycle state
(FR-014).
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.director_context import (
    ChapterBrief,
    CurrentChapterInput,
    DirectorContext,
    SeasonInput,
    TwistInput,
)
from app.domain.director_prompts import load_system_prompt, render_user_prompt
from app.domain.director_verdicts import DirectorBatchResponse, DirectorVerdict
from app.domain.slur_list import matches_slur
from app.infra.cycles_repo import CyclesRepo
from app.infra.twists_repo import Twist, TwistsRepo, VerdictUpdate
from app.providers.llm.router import LLMProviderRouter

DEFAULT_DIRECTOR_BATCH_SIZE = 25
RECENT_CHAPTERS_LIMIT = 3

DEFAULT_DENY_REASON = "No clasificado por el filtro (fail-closed)."
SLUR_OVERRIDE_REASON = "Post-filter: contenido inadecuado."

_REASON_MAX_LEN = 80

_log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Result summary
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FilterSummary:
    """Aggregated counters produced by a single filter run.

    Returned to T-010 (so it can shape the structured log) and to T-011
    (so the admin endpoint can return the ``breakdown`` field).

    ``default_denied`` is a subset of ``rejected_incoherent`` and
    ``slur_overrides`` is a subset of ``rejected_offensive`` — they're
    surfaced separately for observability.
    """

    chapter_id: int
    twist_count: int
    batches: int
    approved: int
    rejected_offensive: int
    rejected_incoherent: int
    rejected_spam: int
    default_denied: int
    slur_overrides: int
    duration_ms: int


# ---------------------------------------------------------------------------
# Context loader (SQL direct — Season/Chapter repos don't project blobs)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ContextSkeleton:
    """Internal: everything the prompt needs except the per-batch twists."""

    season: SeasonInput
    last_chapters: list[ChapterBrief]
    current: CurrentChapterInput


async def _load_context_skeleton(
    session: AsyncSession, chapter_id: int
) -> _ContextSkeleton:
    """Load season bible, current chapter, and last 3 previous chapters.

    Direct SQL is used because :class:`SeasonsRepo` and :class:`ChaptersRepo`
    intentionally exclude JSONB blobs (``bible_json``, ``manifest_json``)
    from their projections to keep the public APIs cheap. The director is
    the only consumer that needs the full blobs, so we read them here
    instead of broadening the public repos.
    """
    current_row = (
        await session.execute(
            sa.text(
                "SELECT c.season_id, c.day_index, c.title, c.synopsis, "
                "       c.manifest_json, s.bible_json "
                "FROM chapters c "
                "JOIN seasons s ON s.id = c.season_id "
                "WHERE c.id = :id"
            ),
            {"id": chapter_id},
        )
    ).mappings().one()

    bible = _coerce_json_dict(current_row["bible_json"])
    manifest = _coerce_json_dict(current_row["manifest_json"])
    season_id = int(current_row["season_id"])
    current_day_index = int(current_row["day_index"])

    last_rows = (
        await session.execute(
            sa.text(
                "SELECT day_index, title, synopsis FROM chapters "
                "WHERE season_id = :sid AND day_index < :di "
                "ORDER BY day_index DESC LIMIT :lim"
            ),
            {
                "sid": season_id,
                "di": current_day_index,
                "lim": RECENT_CHAPTERS_LIMIT,
            },
        )
    ).mappings().all()

    last_chapters = [
        ChapterBrief(
            day_index=int(r["day_index"]),
            title=str(r["title"]),
            synopsis=str(r["synopsis"]),
        )
        for r in reversed(last_rows)
    ]

    current = CurrentChapterInput(
        day_index=current_day_index,
        title=str(current_row["title"]),
        synopsis=str(current_row["synopsis"]),
        manifest_json=manifest,
    )
    return _ContextSkeleton(
        season=SeasonInput(bible_json=bible),
        last_chapters=last_chapters,
        current=current,
    )


def _coerce_json_dict(value: Any) -> dict[str, Any]:
    """asyncpg returns JSONB as a parsed dict; some drivers hand back a str.

    Normalise so the prompt template always sees ``dict[str, Any]``.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        loaded = json.loads(value)
        if isinstance(loaded, dict):
            return loaded
    return {}


# ---------------------------------------------------------------------------
# Batch + verdict mapping
# ---------------------------------------------------------------------------


def _chunked(items: list[Twist], size: int) -> Iterator[list[Twist]]:
    if size <= 0:
        raise ValueError(f"batch_size must be positive, got {size}")
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _attach_batch(
    skeleton: _ContextSkeleton, batch: Iterable[Twist]
) -> DirectorContext:
    """Bind the skeleton to the per-batch twists for the prompt template."""
    return DirectorContext(
        season=skeleton.season,
        last_chapters=skeleton.last_chapters,
        current=skeleton.current,
        batch=[TwistInput(public_id=t.public_id, content=t.content) for t in batch],
    )


def _index_verdicts_for_batch(
    response: DirectorBatchResponse, batch: list[Twist]
) -> dict[UUID, DirectorVerdict]:
    """Index verdicts by ``twist_id`` keeping only ids that are in *batch*.

    Anything the LLM hallucinates (a ``twist_id`` we never sent) is logged
    and dropped — the spec ``Edge Cases`` paragraph calls this out.
    """
    batch_ids = {t.public_id for t in batch}
    by_id: dict[UUID, DirectorVerdict] = {}
    for v in response.verdicts:
        if v.twist_id not in batch_ids:
            _log.warning(
                "director_verdict_unknown_twist_id",
                twist_id=str(v.twist_id),
            )
            continue
        by_id[v.twist_id] = v
    return by_id


def _truncate_reason(reason: str) -> tuple[str, bool]:
    """Clamp *reason* to 80 chars with ``…`` suffix. Return ``(reason, truncated)``."""
    if len(reason) <= _REASON_MAX_LEN:
        return reason, False
    return reason[: _REASON_MAX_LEN - 1] + "…", True


def _build_updates(
    batch: list[Twist],
    verdicts_by_id: dict[UUID, DirectorVerdict],
    counts: _Counts,
) -> list[VerdictUpdate]:
    """Compose final ``VerdictUpdate`` list applying:
      * default-deny for missing twist_ids (FR-009)
      * slur post-filter on ``approved`` (FR-010)
      * reason truncation to 80 chars (Edge Cases)
    """
    updates: list[VerdictUpdate] = []
    for twist in batch:
        verdict = verdicts_by_id.get(twist.public_id)
        if verdict is None:
            counts.add("rejected_incoherent", default_denied=True)
            updates.append(
                VerdictUpdate(
                    twist_id=twist.id,
                    decision="rejected_incoherent",
                    reason=DEFAULT_DENY_REASON,
                )
            )
            continue

        decision: str = verdict.decision
        reason, truncated = _truncate_reason(verdict.reason)
        if truncated:
            _log.info(
                "director_reason_truncated",
                twist_public_id=str(twist.public_id),
            )

        if decision == "approved" and matches_slur(twist.content):
            _log.info(
                "slur_override_applied",
                twist_public_id=str(twist.public_id),
            )
            decision = "rejected_offensive"
            reason = SLUR_OVERRIDE_REASON
            counts.add("rejected_offensive", slur_override=True)
        else:
            counts.add(decision)

        updates.append(
            VerdictUpdate(
                twist_id=twist.id, decision=decision, reason=reason
            )
        )
    return updates


# ---------------------------------------------------------------------------
# Mutable counter (private)
# ---------------------------------------------------------------------------


class _Counts:
    """Tally of decisions taken across all batches of a single run."""

    def __init__(self) -> None:
        self.approved = 0
        self.rejected_offensive = 0
        self.rejected_incoherent = 0
        self.rejected_spam = 0
        self.default_denied = 0
        self.slur_overrides = 0

    def add(
        self,
        decision: str,
        *,
        default_denied: bool = False,
        slur_override: bool = False,
    ) -> None:
        if decision == "approved":
            self.approved += 1
        elif decision == "rejected_offensive":
            self.rejected_offensive += 1
        elif decision == "rejected_incoherent":
            self.rejected_incoherent += 1
        elif decision == "rejected_spam":
            self.rejected_spam += 1
        if default_denied:
            self.default_denied += 1
        if slur_override:
            self.slur_overrides += 1


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_director_filter(
    chapter_id: int,
    *,
    session: AsyncSession,
    router: LLMProviderRouter,
    batch_size: int = DEFAULT_DIRECTOR_BATCH_SIZE,
    allow_already_classified: bool = False,
) -> FilterSummary:
    """Run the director's filter pipeline against *chapter_id*.

    The caller owns the ``AsyncSession`` lifecycle: this function commits
    once per batch (so a mid-run failure leaves earlier batches
    persisted and ``update_status_bulk`` skips already-classified twists
    on retry), but does NOT rollback on error — propagating exceptions
    let the caller / ``safe_side_effect`` wrapper map the failure to a
    cycle transition.

    Parameters
    ----------
    chapter_id:
        Internal chapter id (``chapters.id``).
    session:
        Active async session.
    router:
        Configured :class:`LLMProviderRouter`. In production this is
        ``[GeminiProvider, GitHubModelsProvider]``; tests use
        :class:`FakeLLMProvider`.
    batch_size:
        Twists per LLM call. Default 25 (FR-005, R-009).
    allow_already_classified:
        ``False`` (default) reads only ``pending_review`` twists and
        guards ``UPDATE``s on the same status — safe for the FSM
        side-effect path. ``True`` reads every non-deleted twist and
        relaxes the guard — used by the admin replay endpoint (T-011);
        does NOT touch already-deleted rows.
    """
    started_at = time.monotonic()

    twists_repo = TwistsRepo(session)
    if allow_already_classified:
        twists = await twists_repo.list_all_for_chapter_for_replay(chapter_id)
    else:
        twists = await twists_repo.list_pending_for_chapter(chapter_id)

    _log.info(
        "filter_started", chapter_id=chapter_id, twist_count=len(twists)
    )

    if not twists:
        _log.info("filter_skipped_empty_batch", chapter_id=chapter_id)
        return _empty_summary(chapter_id, started_at)

    skeleton = await _load_context_skeleton(session, chapter_id)
    system_prompt = load_system_prompt()

    counts = _Counts()
    batches_count = 0

    for batch_idx, batch in enumerate(_chunked(twists, batch_size)):
        batches_count += 1
        ctx = _attach_batch(skeleton, batch)
        user_prompt = render_user_prompt(ctx)

        response = await router.chat_json(
            system=system_prompt,
            user=user_prompt,
            response_schema=DirectorBatchResponse,
            temperature=0.2,
            max_output_tokens=2048,
        )

        body = response.content
        assert isinstance(body, DirectorBatchResponse)  # router preserves typing
        _log.info(
            "llm_batch",
            batch_idx=batch_idx,
            provider=response.provider,
            model=response.model,
            latency_ms=response.latency_ms,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
        )

        verdicts_by_id = _index_verdicts_for_batch(body, batch)
        updates = _build_updates(batch, verdicts_by_id, counts)
        await twists_repo.update_status_bulk(
            updates, allow_already_classified=allow_already_classified
        )
        await session.commit()

    duration_ms = int((time.monotonic() - started_at) * 1000)
    summary = FilterSummary(
        chapter_id=chapter_id,
        twist_count=len(twists),
        batches=batches_count,
        approved=counts.approved,
        rejected_offensive=counts.rejected_offensive,
        rejected_incoherent=counts.rejected_incoherent,
        rejected_spam=counts.rejected_spam,
        default_denied=counts.default_denied,
        slur_overrides=counts.slur_overrides,
        duration_ms=duration_ms,
    )
    _log.info(
        "filter_completed",
        chapter_id=chapter_id,
        twist_count=summary.twist_count,
        batches=summary.batches,
        approved=summary.approved,
        rejected_offensive=summary.rejected_offensive,
        rejected_incoherent=summary.rejected_incoherent,
        rejected_spam=summary.rejected_spam,
        default_denied=summary.default_denied,
        slur_overrides=summary.slur_overrides,
        duration_ms=summary.duration_ms,
    )
    return summary


def _empty_summary(chapter_id: int, started_at: float) -> FilterSummary:
    return FilterSummary(
        chapter_id=chapter_id,
        twist_count=0,
        batches=0,
        approved=0,
        rejected_offensive=0,
        rejected_incoherent=0,
        rejected_spam=0,
        default_denied=0,
        slur_overrides=0,
        duration_ms=int((time.monotonic() - started_at) * 1000),
    )


# ---------------------------------------------------------------------------
# Side-effect adapter (T-010 wires this into the side_effects registry)
# ---------------------------------------------------------------------------


def build_director_filter_side_effect(
    session_factory: async_sessionmaker[AsyncSession],
    router: LLMProviderRouter,
) -> Callable[[int], Awaitable[None]]:
    """Return a real ``director_filter`` side-effect bound to *router*.

    Module 006 / Task T-010.

    The side-effects registry (module 003) expects an
    ``async (chapter_id: int) -> None`` callable. This adapter:

      1. Opens a fresh ``AsyncSession`` from *session_factory* — the
         HTTP request's session is long-closed by the time the FastAPI
         ``BackgroundTask`` invokes us.
      2. Runs the T-009 orchestrator (which commits per batch).
      3. Looks up the ``cycle_id`` so the trigger_id matches FR-012's
         shape ``"director-{cycle_id}-{uuid}"``. ``state_transitions``
         has a UNIQUE on ``trigger_id``, so a hypothetical retry would
         hit ``already_applied`` instead of double-transitioning.
      4. Calls :func:`app.domain.cycle_executor.transition` with
         ``requested_to='VOTACION'`` and ``triggered_by='side_effect'``.

    Exceptions propagate. Module 003's ``safe_side_effect`` wrapper
    catches them and drives the cycle to ``FAILED``.
    """
    # Import locally to avoid a circular at module-import time.
    from app.domain.cycle_executor import transition

    async def _real_director_filter(chapter_id: int) -> None:
        async with session_factory() as session:
            await run_director_filter(
                chapter_id, session=session, router=router
            )

            cycle = await CyclesRepo(session).get_by_chapter_id(chapter_id)
            if cycle is None:
                raise RuntimeError(
                    f"director_filter completed for chapter_id={chapter_id} "
                    "but no cycle references it; cannot transition to VOTACION"
                )

            await transition(
                session,
                requested_to="VOTACION",
                triggered_by="side_effect",
                trigger_id=f"director-{cycle.id}-{uuid4()}",
            )

    return _real_director_filter
