"""Integration tests: TwistSubmissionService.delete — happy + gates.

Module 005 / Task T-006.

Skips when DATABASE_URL is the conftest placeholder. Each test creates a
fresh active season + live chapter + cycle in RECEPCION_IDEAS and cleans
up in ``finally``.

Coverage:
  - delete() persists status='deleted_by_user' + deleted_at; quota NOT freed.
  - Re-delete is idempotent (was_idempotent=True, same deleted_at).
  - Unknown public_id raises TwistNotFound.
  - Other user's twist raises ForbiddenNotOwner.
  - Kill switch raises KillSwitchActive.
  - Wrong cycle state raises WindowClosed.
"""
# ruff: noqa: F811 — pytest fixtures are re-imported by name for collection.

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.twist_submission import (
    DeleteResult,
    ForbiddenNotOwner,
    KillSwitchActive,
    SubmitResult,
    TwistNotFound,
    TwistSubmissionService,
    WindowClosed,
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


async def test_delete_happy_path(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "del-happy-001"
    )
    user = await make_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    try:
        submit = await _submit_one(
            service,
            user_id=user[0],
            chapter_public_id=chapter_public_id,
            content="Esta idea voy a borrar",
        )
        assert submit.quota.used == 1

        result = await service.delete(
            user_id=user[0],
            twist_public_id=submit.twist.public_id,
        )
        assert isinstance(result, DeleteResult)
        assert result.was_idempotent is False
        assert result.deleted_at is not None
        # Quota does NOT decrement (FR-004 / FR-009).
        assert result.quota.used == 1

        # DB row reflects deleted_by_user + deleted_at.
        row = await setup_session.execute(
            sa.text(
                "SELECT status, deleted_at FROM twists "
                "WHERE public_id = :pid"
            ),
            {"pid": str(submit.twist.public_id)},
        )
        mp = row.mappings().one()
        assert mp["status"] == "deleted_by_user"
        assert mp["deleted_at"] is not None
    finally:
        await cleanup(setup_session, season_id, user)


async def test_delete_is_idempotent(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "del-idem-001"
    )
    user = await make_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    try:
        submit = await _submit_one(
            service,
            user_id=user[0],
            chapter_public_id=chapter_public_id,
            content="Para borrar dos veces",
        )
        first = await service.delete(user[0], submit.twist.public_id)
        second = await service.delete(user[0], submit.twist.public_id)

        assert first.was_idempotent is False
        assert second.was_idempotent is True
        # Same timestamp on replay — the second call does not refresh deleted_at.
        assert first.deleted_at == second.deleted_at
        assert second.quota.used == 1
    finally:
        await cleanup(setup_session, season_id, user)


async def test_delete_unknown_public_id_raises_not_found(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, _ = await make_active_recepcion_setup(
        setup_session, "del-nf-001"
    )
    user = await make_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    try:
        with pytest.raises(TwistNotFound):
            await service.delete(user[0], uuid4())
    finally:
        await cleanup(setup_session, season_id, user)


async def test_delete_other_users_twist_raises_forbidden(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "del-forb-001"
    )
    owner = await make_user(setup_session)
    other = await make_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    try:
        submit = await _submit_one(
            service,
            user_id=owner[0],
            chapter_public_id=chapter_public_id,
            content="Idea del owner solo",
        )
        with pytest.raises(ForbiddenNotOwner):
            await service.delete(other[0], submit.twist.public_id)
    finally:
        await cleanup(setup_session, season_id, owner, other)


async def test_delete_raises_kill_switch_active(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "del-kill-001"
    )
    user = await make_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    try:
        submit = await _submit_one(
            service,
            user_id=user[0],
            chapter_public_id=chapter_public_id,
            content="Antes del kill switch",
        )
        # Flip kill switch ON.
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

        with pytest.raises(KillSwitchActive):
            await service.delete(user[0], submit.twist.public_id)
    finally:
        # Reset kill switch.
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


async def test_delete_raises_window_closed_when_cycle_in_filtering(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "del-wc-001"
    )
    user = await make_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    try:
        submit = await _submit_one(
            service,
            user_id=user[0],
            chapter_public_id=chapter_public_id,
            content="Pre-FILTERING borrame",
        )
        # Move cycle to FILTERING — delete window no longer open.
        await setup_session.execute(
            sa.text(
                "UPDATE cycles SET state = 'FILTERING', state_entered_at = now() "
                "WHERE season_id = :sid"
            ),
            {"sid": season_id},
        )
        await setup_session.commit()

        with pytest.raises(WindowClosed):
            await service.delete(user[0], submit.twist.public_id)
    finally:
        await cleanup(setup_session, season_id, user)
