"""Integration tests: TwistSubmissionService.list_mine — /me/twists service.

Module 005 / Task T-006.

Coverage:
  - Returns user's twists for the live chapter with quota snapshot.
  - Empty list when user has no twists.
  - ORDER BY submitted_at ASC, includes deleted_by_user rows.
  - Kill-switch raises.
  - When no live chapter exists, returns empty + quota=0/max (no raise).
  - Isolated per user.
"""
# ruff: noqa: F811 — pytest fixtures are re-imported by name for collection.

from __future__ import annotations

from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.twist_quota import QuotaState
from app.domain.twist_submission import (
    KillSwitchActive,
    ListMineResult,
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


async def _submit_one(
    service: TwistSubmissionService,
    *,
    user_id: int,
    chapter_public_id: UUID,
    content: str,
) -> SubmitResult:
    body = {"chapter_id": str(chapter_public_id), "content": content}
    return await service.submit(
        user_id=user_id,
        chapter_public_id=chapter_public_id,
        content=content,
        idempotency_key=fresh_idempotency_key(),
        idempotency_body_hash=body_hash(body),
    )


# ---------------------------------------------------------------------------


async def test_list_returns_user_twists_with_quota(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two pending submits + 1 soft-deleted are all returned; quota=3/3."""
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "me-happy-001"
    )
    user = await make_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    try:
        s1 = await _submit_one(
            service,
            user_id=user[0],
            chapter_public_id=chapter_public_id,
            content="idea uno xxxx",
        )
        await _submit_one(
            service,
            user_id=user[0],
            chapter_public_id=chapter_public_id,
            content="idea dos xxxx",
        )
        await _submit_one(
            service,
            user_id=user[0],
            chapter_public_id=chapter_public_id,
            content="idea tres xxxx",
        )
        await service.delete(user[0], s1.twist.public_id)

        result = await service.list_mine(user[0])
        assert isinstance(result, ListMineResult)
        assert len(result.items) == 3
        # Quota=3/3, deletes don't free.
        assert result.quota == QuotaState(used=3, max=3)
        # Statuses: 1 deleted + 2 pending_review.
        statuses = {t.status for t in result.items}
        assert statuses == {"deleted_by_user", "pending_review"}
    finally:
        await cleanup(setup_session, season_id, user)


async def test_list_empty_for_user_without_twists(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, _ = await make_active_recepcion_setup(
        setup_session, "me-empty-001"
    )
    user = await make_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    try:
        result = await service.list_mine(user[0])
        assert result.items == []
        assert result.quota == QuotaState(used=0, max=3)
    finally:
        await cleanup(setup_session, season_id, user)


async def test_list_ordered_by_submitted_at_asc(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "me-order-001"
    )
    user = await make_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    try:
        a = await _submit_one(
            service,
            user_id=user[0],
            chapter_public_id=chapter_public_id,
            content="primera xxxx",
        )
        b = await _submit_one(
            service,
            user_id=user[0],
            chapter_public_id=chapter_public_id,
            content="segunda xxxx",
        )
        c = await _submit_one(
            service,
            user_id=user[0],
            chapter_public_id=chapter_public_id,
            content="tercera xxxx",
        )

        result = await service.list_mine(user[0])
        ids = [t.id for t in result.items]
        assert ids == [a.twist.id, b.twist.id, c.twist.id]
    finally:
        await cleanup(setup_session, season_id, user)


async def test_list_raises_kill_switch(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, _ = await make_active_recepcion_setup(
        setup_session, "me-kill-001"
    )
    user = await make_user(setup_session)
    await setup_session.execute(
        sa.text(
            "UPDATE system_flags SET flag_value = "
            "cast('{\"on\": true, \"reason\": \"test\"}' AS jsonb), "
            "updated_by = 'test', updated_at = now() "
            "WHERE flag_key = 'kill_switch'"
        )
    )
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    try:
        with pytest.raises(KillSwitchActive):
            await service.list_mine(user[0])
    finally:
        await setup_session.execute(
            sa.text(
                "UPDATE system_flags SET flag_value = "
                "cast('{\"on\": false, \"reason\": null}' AS jsonb), "
                "updated_by = 'test', updated_at = now() "
                "WHERE flag_key = 'kill_switch'"
            )
        )
        await setup_session.commit()
        clear_flags_cache()
        await cleanup(setup_session, season_id, user)


async def test_list_returns_empty_when_no_live_chapter(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """No live chapter → empty items + quota 0/max (defensive, no raise)."""
    season_id, _, _ = await make_active_recepcion_setup(
        setup_session, "me-nochap-001"
    )
    user = await make_user(setup_session)
    # Demote the chapter from 'live' to 'ready'.
    await setup_session.execute(
        sa.text(
            "UPDATE chapters SET status = 'ready' WHERE season_id = :sid"
        ),
        {"sid": season_id},
    )
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    try:
        result = await service.list_mine(user[0])
        assert result.items == []
        assert result.quota == QuotaState(used=0, max=3)
    finally:
        await cleanup(setup_session, season_id, user)
