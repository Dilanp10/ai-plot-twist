"""Integration tests: TwistSubmissionService.delete — already-filtered guard.

Module 005 / Task T-006.

Once the LLM filter (module 006, future) transitions a twist out of
``pending_review``, the twist becomes immutable. delete() must reject
with :class:`AlreadyFiltered`. We simulate the filter by UPDATEing the
status directly via SQL (module 006 ships the real path).
"""
# ruff: noqa: F811 — pytest fixtures are re-imported by name for collection.

from __future__ import annotations

from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.twist_submission import (
    AlreadyFiltered,
    SubmitResult,
    TwistSubmissionService,
)
from app.domain.windows import CycleTimes
from app.infra.system_flags_repo import clear_cache as clear_flags_cache

from ._twist_submit_helpers import (
    NOW_IN_WINDOW,
    _ensure_migrated,  # noqa: F401
    body_hash,
    cleanup,
    database_url,  # noqa: F401
    fresh_idempotency_key,
    make_active_recepcion_setup,
    make_user,
    session_factory,  # noqa: F401
    setup_session,  # noqa: F401
)


def _service(
    session_factory_: async_sessionmaker[AsyncSession],
) -> TwistSubmissionService:
    return TwistSubmissionService(
        session_factory=session_factory_,
        cycle_times=CycleTimes.default(),
        max_per_chapter=3,
        now_utc=lambda: NOW_IN_WINDOW,
    )


async def _submit_then_set_status(
    setup_session_: AsyncSession,
    service: TwistSubmissionService,
    *,
    user_id: int,
    chapter_public_id: UUID,
    target_status: str,
) -> SubmitResult:
    body = {"chapter_id": str(chapter_public_id), "content": "Idea para filtrar"}
    result = await service.submit(
        user_id=user_id,
        chapter_public_id=chapter_public_id,
        content=body["content"],
        idempotency_key=fresh_idempotency_key(),
        idempotency_body_hash=body_hash(body),
    )
    # Simulate the LLM filter: mark the twist as approved/rejected.
    await setup_session_.execute(
        sa.text(
            "UPDATE twists SET status = :st, reviewed_at = now() "
            "WHERE public_id = :pid"
        ),
        {"st": target_status, "pid": str(result.twist.public_id)},
    )
    await setup_session_.commit()
    return result


# ---------------------------------------------------------------------------


async def test_delete_after_approved_raises_already_filtered(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "af-approved-001"
    )
    user = await make_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    try:
        submit = await _submit_then_set_status(
            setup_session,
            service,
            user_id=user[0],
            chapter_public_id=chapter_public_id,
            target_status="approved",
        )
        with pytest.raises(AlreadyFiltered):
            await service.delete(user[0], submit.twist.public_id)
    finally:
        await cleanup(setup_session, season_id, user)


async def test_delete_after_rejected_raises_already_filtered(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "af-rejected-001"
    )
    user = await make_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    try:
        submit = await _submit_then_set_status(
            setup_session,
            service,
            user_id=user[0],
            chapter_public_id=chapter_public_id,
            target_status="rejected_offensive",
        )
        with pytest.raises(AlreadyFiltered):
            await service.delete(user[0], submit.twist.public_id)
    finally:
        await cleanup(setup_session, season_id, user)
