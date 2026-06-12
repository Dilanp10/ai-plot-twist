"""Integration tests: TwistSubmissionService.submit — idempotency.

Module 005 / Task T-005.

Coverage:
  - Same key + same body hash → cached SubmitResult, was_replay=True, no DB insert.
  - Same key + different body hash → IdempotencyConflict.
  - Fresh key after first submit → fresh insert.
  - Replay does not consume quota.
"""
# ruff: noqa: F811 — pytest fixtures are re-imported by name for collection.

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.twist_submission import (
    IdempotencyConflict,
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


# ---------------------------------------------------------------------------


async def test_replay_same_key_same_hash_returns_cached(
    setup_session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    season_id, chapter_id, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "idem-same-001"
    )
    user = await make_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    body = {"chapter_id": str(chapter_public_id), "content": "Idea para replay"}
    idem_key = fresh_idempotency_key()
    hash_ = body_hash(body)

    try:
        first = await service.submit(
            user_id=user[0],
            chapter_public_id=chapter_public_id,
            content=body["content"],
            idempotency_key=idem_key,
            idempotency_body_hash=hash_,
        )
        # Same key + same hash → cached.
        second = await service.submit(
            user_id=user[0],
            chapter_public_id=chapter_public_id,
            content=body["content"],
            idempotency_key=idem_key,
            idempotency_body_hash=hash_,
        )
        assert first.was_replay is False
        assert second.was_replay is True
        assert second.twist.public_id == first.twist.public_id
        assert second.quota.used == 1

        # DB has exactly 1 twist row.
        row = await setup_session.execute(
            sa.text(
                "SELECT COUNT(*) FROM twists "
                "WHERE user_id = :uid AND chapter_id = :cid"
            ),
            {"uid": user[0], "cid": chapter_id},
        )
        assert int(row.scalar_one()) == 1
    finally:
        await cleanup(setup_session, season_id, user)


async def test_replay_same_key_diff_hash_raises_conflict(
    setup_session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "idem-diff-001"
    )
    user = await make_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    body_a = {"chapter_id": str(chapter_public_id), "content": "Cuerpo A"}
    body_b = {"chapter_id": str(chapter_public_id), "content": "Cuerpo B distinto"}
    idem_key = fresh_idempotency_key()

    try:
        await service.submit(
            user_id=user[0],
            chapter_public_id=chapter_public_id,
            content=body_a["content"],
            idempotency_key=idem_key,
            idempotency_body_hash=body_hash(body_a),
        )
        with pytest.raises(IdempotencyConflict):
            await service.submit(
                user_id=user[0],
                chapter_public_id=chapter_public_id,
                content=body_b["content"],
                idempotency_key=idem_key,
                idempotency_body_hash=body_hash(body_b),
            )
    finally:
        await cleanup(setup_session, season_id, user)


async def test_fresh_key_inserts_a_second_twist(
    setup_session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    season_id, chapter_id, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "idem-fresh-001"
    )
    user = await make_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    body = {"chapter_id": str(chapter_public_id), "content": "Idea uno xxxx"}

    try:
        await service.submit(
            user_id=user[0],
            chapter_public_id=chapter_public_id,
            content=body["content"],
            idempotency_key=fresh_idempotency_key(),
            idempotency_body_hash=body_hash(body),
        )
        body2 = {"chapter_id": str(chapter_public_id), "content": "Idea dos xxxx"}
        result2 = await service.submit(
            user_id=user[0],
            chapter_public_id=chapter_public_id,
            content=body2["content"],
            idempotency_key=fresh_idempotency_key(),
            idempotency_body_hash=body_hash(body2),
        )
        assert result2.was_replay is False
        assert result2.quota.used == 2

        row = await setup_session.execute(
            sa.text(
                "SELECT COUNT(*) FROM twists "
                "WHERE user_id = :uid AND chapter_id = :cid"
            ),
            {"uid": user[0], "cid": chapter_id},
        )
        assert int(row.scalar_one()) == 2
    finally:
        await cleanup(setup_session, season_id, user)


async def test_replay_does_not_consume_quota(
    setup_session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """3 replays of the same idempotent submit must leave quota at 1, not 3."""
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "idem-quota-001"
    )
    user = await make_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    body = {"chapter_id": str(chapter_public_id), "content": "Una sola xxxx"}
    idem_key = fresh_idempotency_key()
    hash_ = body_hash(body)

    try:
        for _ in range(4):
            result = await service.submit(
                user_id=user[0],
                chapter_public_id=chapter_public_id,
                content=body["content"],
                idempotency_key=idem_key,
                idempotency_body_hash=hash_,
            )
        # Final quota is still 1/3 — only the first was a fresh insert.
        assert result.quota.used == 1
        assert result.quota.remaining == 2
    finally:
        await cleanup(setup_session, season_id, user)
