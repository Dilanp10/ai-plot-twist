"""Push fan-out orchestrator (FR-008).

Module 011 / Task T-006.

Runs once per ``ESTRENO`` transition (module 003 executor spawns it as
a best-effort BackgroundTask via T-010). The orchestrator:

  1. Loads the chapter's public_id + title + day_index so the payload
     can address the device.
  2. Claims an idempotency row keyed ``push_fanout:<chapter_public_id>``
     — a re-fire of the same chapter transition short-circuits with
     ``skipped_idempotent=True`` and never touches the push services
     (constitution Gate 2).
  3. Loads every active subscription (non-banned users).
  4. Composes the FR-010 payload **once** and reuses the serialised
     bytes across every send — cheaper than re-encoding per-sub and
     guarantees every browser receives an identical body.
  5. Runs the sends with bounded concurrency (a Semaphore) under a
     wall-clock deadline. Past the deadline, pending sends are
     cancelled and their subscriptions go untouched — the next
     fan-out (next chapter) will revisit them.
  6. Applies the per-subscription outcome to the repo: mark_success /
     mark_failure / bulk_delete (Gone).
  7. Finishes with a stale-cleanup pass per R-005.

Testability seams:
  - :func:`_load_chapter_meta` — patchable for unit tests so they
    don't need a chapters/season row.
  - :func:`_claim_idempotency` — patchable for the idempotency
    short-circuit test.

All internal helpers use the ``_`` prefix so they appear at module
scope and ``unittest.mock.patch`` can target them.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.push_payload import compose_chapter_notification
from app.infra.push_subscriptions_repo import (
    PushSubscriptionsRepo,
    Subscription,
)
from app.infra.webpush_sender import SendOutcome, SendResult, WebPushSender

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FanoutSummary:
    """Aggregate outcome of one fan-out.

    Returned from :func:`run_push_fanout` for the admin test endpoint
    (T-009) and for log-event composition. The side-effect factory
    ignores the return value (its closure adapts the signature to
    ``Callable[[int], Awaitable[None]]``).
    """

    sent: int
    failed: int
    gone: int
    cleaned: int
    deadline_exceeded: bool
    skipped_idempotent: bool


@dataclass(frozen=True)
class _ChapterMeta:
    public_id: UUID
    title: str
    day_index: int


# ---------------------------------------------------------------------------
# Internal helpers (patch seams)
# ---------------------------------------------------------------------------


async def _load_chapter_meta(
    session: AsyncSession, chapter_id: int
) -> _ChapterMeta:
    """Return the columns needed to compose the payload.

    Raises ``sqlalchemy.exc.NoResultFound`` when chapter_id is invalid.
    Callers wrap the fan-out in a safe boundary (module 003's
    safe_side_effect) so unknown ids degrade to a logged failure.
    """
    row = (
        await session.execute(
            sa.text(
                "SELECT public_id, title, day_index "
                "FROM chapters "
                "WHERE id = :cid"
            ),
            {"cid": chapter_id},
        )
    ).mappings().one()
    return _ChapterMeta(
        public_id=UUID(str(row["public_id"])),
        title=str(row["title"]),
        day_index=int(row["day_index"]),
    )


async def _claim_idempotency(
    session: AsyncSession, chapter_public_id: UUID
) -> bool:
    """Insert the ``push_fanout:<uuid>`` row, return True when claimed.

    ``ON CONFLICT (key) DO NOTHING`` makes a re-fire of the same
    chapter return 0 rows — the orchestrator short-circuits in that
    case. The body hash is empty (system-scoped, no user payload) and
    user_id is NULL — both consistent with module 001's table contract.
    """
    started_at = datetime.now(UTC).isoformat()
    result = await session.execute(
        sa.text(
            "INSERT INTO idempotency_keys "
            "  (key, user_id, request_hash, response_json) "
            "VALUES "
            "  ('push_fanout:' || :uuid, NULL, '', "
            "   cast(:body AS jsonb)) "
            "ON CONFLICT (key) DO NOTHING "
            "RETURNING key"
        ),
        {
            "uuid": str(chapter_public_id),
            "body": json.dumps({"started_at": started_at}),
        },
    )
    return result.one_or_none() is not None


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


async def run_push_fanout(
    chapter_id: int,
    *,
    session: AsyncSession,
    sender: WebPushSender,
    timeout_s: float = 60.0,
    threshold: int = 3,
    concurrency: int = 8,
) -> FanoutSummary:
    """Fan a chapter-released push out to every active subscription.

    Parameters
    ----------
    chapter_id:
        Internal chapter id of the just-released chapter.
    session:
        An open ``AsyncSession``. The function commits the final
        accounting transaction.
    sender:
        Pre-wired :class:`~app.infra.webpush_sender.WebPushSender`.
    timeout_s:
        Wall-clock deadline. Past this, in-flight sends are cancelled.
    threshold:
        Failure count above which a stale subscription is culled
        (subject to the 7-day floor in R-005).
    concurrency:
        Max parallel sends (asyncio.Semaphore size).
    """
    chapter = await _load_chapter_meta(session, chapter_id)
    logger.info(
        "push_fanout_started chapter_id=%d public_id=%s day_index=%d",
        chapter_id,
        chapter.public_id,
        chapter.day_index,
    )

    if not await _claim_idempotency(session, chapter.public_id):
        logger.info(
            "push_fanout_skipped_idempotent chapter_id=%d public_id=%s",
            chapter_id,
            chapter.public_id,
        )
        await session.commit()
        return FanoutSummary(
            sent=0,
            failed=0,
            gone=0,
            cleaned=0,
            deadline_exceeded=False,
            skipped_idempotent=True,
        )

    repo = PushSubscriptionsRepo(session)
    subscriptions = await repo.list_active_all()

    if not subscriptions:
        logger.info("push_fanout_no_subscriptions chapter_id=%d", chapter_id)
        await session.commit()
        return FanoutSummary(
            sent=0,
            failed=0,
            gone=0,
            cleaned=0,
            deadline_exceeded=False,
            skipped_idempotent=False,
        )

    payload = compose_chapter_notification(
        chapter_public_id=chapter.public_id,
        chapter_title=chapter.title,
        day_index=chapter.day_index,
    )
    # Serialise once so every browser receives a byte-identical body
    # (push services hash + cache the encrypted ciphertext).
    payload_bytes = json.dumps(payload).encode("utf-8")

    outcomes, deadline_exceeded = await _send_with_deadline(
        subscriptions,
        sender=sender,
        payload_bytes=payload_bytes,
        timeout_s=timeout_s,
        concurrency=concurrency,
    )

    sent, failed, gone_ids = await _apply_outcomes(repo, outcomes)
    cleaned = 0
    if gone_ids:
        await repo.bulk_delete(gone_ids)
    cleaned = await repo.cleanup_stale(threshold=threshold)

    await session.commit()

    logger.info(
        "push_fanout_completed chapter_id=%d sent=%d failed=%d gone=%d "
        "cleaned=%d deadline_exceeded=%s",
        chapter_id,
        sent,
        failed,
        len(gone_ids),
        cleaned,
        deadline_exceeded,
    )

    return FanoutSummary(
        sent=sent,
        failed=failed,
        gone=len(gone_ids),
        cleaned=cleaned,
        deadline_exceeded=deadline_exceeded,
        skipped_idempotent=False,
    )


# ---------------------------------------------------------------------------
# Send loop with deadline + bounded concurrency
# ---------------------------------------------------------------------------


async def _send_with_deadline(
    subscriptions: list[Subscription],
    *,
    sender: WebPushSender,
    payload_bytes: bytes,
    timeout_s: float,
    concurrency: int,
) -> tuple[list[SendOutcome], bool]:
    """Send to every subscription under the wall-clock budget.

    Uses :func:`asyncio.wait` instead of ``wait_for(gather(...))`` so
    partial results survive a timeout: completed sends keep their
    outcomes, pending sends get cancelled and stay un-accounted for
    (their failure_count is not bumped this round — they'll be
    revisited on the next fan-out).
    """
    sem = asyncio.Semaphore(concurrency)

    async def _one(sub: Subscription) -> SendOutcome:
        async with sem:
            return await sender.send(sub, payload_bytes)

    tasks = [asyncio.create_task(_one(s)) for s in subscriptions]
    done, pending = await asyncio.wait(
        tasks, timeout=max(timeout_s, 0.0)
    )
    deadline_exceeded = bool(pending)
    for task in pending:
        task.cancel()
    # Drain cancellations so they don't surface as unhandled-task warnings
    # on the next event loop iteration.
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    outcomes: list[SendOutcome] = []
    for task in done:
        try:
            outcomes.append(task.result())
        except Exception as exc:
            logger.warning(
                "push_fanout_task_unexpected_error error=%s", exc
            )
    return outcomes, deadline_exceeded


async def _apply_outcomes(
    repo: PushSubscriptionsRepo, outcomes: list[SendOutcome]
) -> tuple[int, int, list[int]]:
    """Translate each outcome into the matching repo write.

    Returns ``(sent, failed, gone_ids)``. The orchestrator passes
    ``gone_ids`` to :meth:`bulk_delete` so the next fan-out doesn't
    re-send to dead endpoints.
    """
    sent = 0
    failed = 0
    gone_ids: list[int] = []
    for outcome in outcomes:
        if outcome.result == SendResult.SUCCESS:
            await repo.mark_success(outcome.subscription_id)
            sent += 1
        elif outcome.result == SendResult.GONE:
            gone_ids.append(outcome.subscription_id)
        else:
            await repo.mark_failure(outcome.subscription_id)
            failed += 1
    return sent, failed, gone_ids


# ---------------------------------------------------------------------------
# Side-effect factory (T-010 wires this into the side_effects registry)
# ---------------------------------------------------------------------------


def build_push_fanout_side_effect(
    session_factory: async_sessionmaker[AsyncSession],
    sender: WebPushSender,
    *,
    timeout_s: float,
    threshold: int,
    concurrency: int,
) -> Callable[[int], Awaitable[None]]:
    """Return a ``push_fanout`` side-effect bound to its dependencies.

    The returned callable matches ``SideEffect = Callable[[int],
    Awaitable[None]]`` expected by :mod:`app.domain.side_effects`.

    Exceptions propagate so module 003's ``safe_side_effect`` wrapper
    can log + alert (the fan-out is best-effort per FR-009; the
    transition to ESTRENO is NOT contingent on it succeeding).
    """

    async def _push_fanout(chapter_id: int) -> None:
        async with session_factory() as session:
            await run_push_fanout(
                chapter_id,
                session=session,
                sender=sender,
                timeout_s=timeout_s,
                threshold=threshold,
                concurrency=concurrency,
            )

    return _push_fanout


# ---------------------------------------------------------------------------
# Module-load stub registration
# ---------------------------------------------------------------------------


async def push_fanout_stub(chapter_id: int) -> None:
    """No-op stub: registered at side_effects import time.

    Lets the FSM cycle through ESTRENO cleanly even when VAPID keys
    are absent (staging, local dev). Module 011 / T-010 overrides
    this with the real :func:`build_push_fanout_side_effect` closure
    when keys are present.
    """
    logger.info(
        "push_fanout_stub chapter_id=%d  "
        "(stub — real impl injected by module 011)",
        chapter_id,
    )
