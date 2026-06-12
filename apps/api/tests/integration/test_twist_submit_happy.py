"""Integration tests: TwistSubmissionService.submit — happy path + gates.

Module 005 / Task T-005.

Skips when DATABASE_URL is the conftest placeholder. Each test creates a
fresh active season + live chapter + cycle in RECEPCION_IDEAS and cleans
up in ``finally``.

Coverage:
  - Submitting a valid twist persists it and returns SubmitResult.
  - NFKC normalization is applied before persistence.
  - At-quota-minus-one submit succeeds (quota.at_capacity True).
  - Kill switch raises ``KillSwitchActive``.
  - Wrong cycle state raises ``WindowClosed``.
"""
# ruff: noqa: F811 — pytest fixtures are re-imported by name for collection.

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.twist_quota import QuotaState
from app.domain.twist_submission import (
    KillSwitchActive,
    SubmitResult,
    TwistSubmissionService,
    WindowClosed,
)
from app.domain.windows import CycleTimes
from app.infra.system_flags_repo import clear_cache as clear_flags_cache

from ._twist_submit_helpers import (
    NOW_IN_WINDOW,
    _ensure_migrated,  # noqa: F401 — re-exported autouse fixture
    body_hash,
    cleanup,
    database_url,  # noqa: F401 — re-exported fixture
    fresh_idempotency_key,
    make_active_recepcion_setup,
    make_user,
    session_factory,  # noqa: F401 — re-exported fixture
    setup_session,  # noqa: F401 — re-exported fixture
)


def _service(
    session_factory_: async_sessionmaker[AsyncSession],
    *,
    max_per_chapter: int = 3,
) -> TwistSubmissionService:
    return TwistSubmissionService(
        session_factory=session_factory_,
        cycle_times=CycleTimes.default(),
        max_per_chapter=max_per_chapter,
        now_utc=lambda: NOW_IN_WINDOW,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_happy_path_inserts_twist(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, chapter_id, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "happy-001"
    )
    user = await make_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    body = {"chapter_id": str(chapter_public_id), "content": "Mi idea brillante"}
    try:
        result = await service.submit(
            user_id=user[0],
            chapter_public_id=chapter_public_id,
            content=body["content"],
            idempotency_key=fresh_idempotency_key(),
            idempotency_body_hash=body_hash(body),
        )

        assert isinstance(result, SubmitResult)
        assert result.was_replay is False
        assert result.twist.chapter_id == chapter_id
        assert result.twist.user_id == user[0]
        assert result.twist.content == "Mi idea brillante"
        assert result.twist.status == "pending_review"
        assert result.quota == QuotaState(used=1, max=3)
    finally:
        await cleanup(setup_session, season_id, user)


async def test_normalizes_content_before_persist(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """NFKC + zero-width strip + trim applied before INSERT."""
    season_id, _chapter_id, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "norm-001"
    )
    user = await make_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    # Leading + trailing spaces, NFKC compatibility, zero-width sneak.
    raw = "  Hola​mundo brillante  "
    body = {"chapter_id": str(chapter_public_id), "content": raw}
    try:
        result = await service.submit(
            user_id=user[0],
            chapter_public_id=chapter_public_id,
            content=raw,
            idempotency_key=fresh_idempotency_key(),
            idempotency_body_hash=body_hash(body),
        )
        assert result.twist.content == "Holamundo brillante"
    finally:
        await cleanup(setup_session, season_id, user)


async def test_at_quota_minus_one_marks_at_capacity(
    setup_session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "cap-001"
    )
    user = await make_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    try:
        for i in range(3):
            body = {"chapter_id": str(chapter_public_id), "content": f"idea {i} xxxx"}
            result = await service.submit(
                user_id=user[0],
                chapter_public_id=chapter_public_id,
                content=body["content"],
                idempotency_key=fresh_idempotency_key(),
                idempotency_body_hash=body_hash(body),
            )
        # After the 3rd: quota=3/3, at capacity.
        assert result.quota == QuotaState(used=3, max=3)
        assert result.quota.at_capacity is True
        assert result.quota.remaining == 0
    finally:
        await cleanup(setup_session, season_id, user)


# ---------------------------------------------------------------------------
# Gates (early-exit exceptions)
# ---------------------------------------------------------------------------


async def test_kill_switch_raises(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "kill-001"
    )
    user = await make_user(setup_session)
    # Turn the kill switch on.
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
    body = {"chapter_id": str(chapter_public_id), "content": "no debería entrar"}
    try:
        import pytest

        with pytest.raises(KillSwitchActive):
            await service.submit(
                user_id=user[0],
                chapter_public_id=chapter_public_id,
                content=body["content"],
                idempotency_key=fresh_idempotency_key(),
                idempotency_body_hash=body_hash(body),
            )
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


async def test_window_closed_when_cycle_state_not_recepcion(
    setup_session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """A cycle in FILTERING blocks new submits."""
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "wrong-state-001"
    )
    # Move the cycle to FILTERING — submits no longer allowed.
    await setup_session.execute(
        sa.text(
            "UPDATE cycles SET state = 'FILTERING', state_entered_at = now() "
            "WHERE season_id = :sid"
        ),
        {"sid": season_id},
    )
    user = await make_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    body = {"chapter_id": str(chapter_public_id), "content": "tarde para esta"}
    try:
        import pytest

        with pytest.raises(WindowClosed):
            await service.submit(
                user_id=user[0],
                chapter_public_id=chapter_public_id,
                content=body["content"],
                idempotency_key=fresh_idempotency_key(),
                idempotency_body_hash=body_hash(body),
            )
    finally:
        await cleanup(setup_session, season_id, user)
