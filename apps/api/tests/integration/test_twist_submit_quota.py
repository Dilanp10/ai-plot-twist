"""Integration tests: TwistSubmissionService.submit — quota enforcement.

Module 005 / Task T-005.

Coverage:
  - Exact-cap submit (3rd of 3) succeeds.
  - Over-cap submit (4th of 3) raises OverQuota.
  - Deleted twists still count toward quota (FR-004).
  - Quota is isolated per user.
  - chapter_public_id mismatching the live chapter raises ChapterMismatch.
"""
# ruff: noqa: F811 — pytest fixtures are re-imported by name for collection.

from __future__ import annotations

from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.twist_submission import (
    ChapterMismatch,
    OverQuota,
    TwistSubmissionService,
)
from app.domain.windows import CycleTimes
from app.infra.system_flags_repo import clear_cache as clear_flags_cache
from app.infra.twists_repo import TwistsRepo

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


async def test_exact_cap_submit_succeeds(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "exact-cap-001"
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
        assert result.quota.used == 3
        assert result.quota.at_capacity is True
    finally:
        await cleanup(setup_session, season_id, user)


async def test_over_cap_submit_raises_over_quota(
    setup_session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "over-cap-001"
    )
    user = await make_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    try:
        for i in range(3):
            body = {"chapter_id": str(chapter_public_id), "content": f"idea {i} xxxx"}
            await service.submit(
                user_id=user[0],
                chapter_public_id=chapter_public_id,
                content=body["content"],
                idempotency_key=fresh_idempotency_key(),
                idempotency_body_hash=body_hash(body),
            )

        body4 = {"chapter_id": str(chapter_public_id), "content": "una cuarta xxx"}
        with pytest.raises(OverQuota) as excinfo:
            await service.submit(
                user_id=user[0],
                chapter_public_id=chapter_public_id,
                content=body4["content"],
                idempotency_key=fresh_idempotency_key(),
                idempotency_body_hash=body_hash(body4),
            )
        assert excinfo.value.used == 3
        assert excinfo.value.max == 3
    finally:
        await cleanup(setup_session, season_id, user)


async def test_deleted_twists_count_toward_quota(
    setup_session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """FR-004 anti-spam-then-delete: deletes do NOT free quota."""
    season_id, chapter_id, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "deleted-count-001"
    )
    user = await make_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    try:
        # Submit 3, then soft-delete 1 directly via repo.
        twist_ids = []
        for i in range(3):
            body = {"chapter_id": str(chapter_public_id), "content": f"idea {i} xxxx"}
            r = await service.submit(
                user_id=user[0],
                chapter_public_id=chapter_public_id,
                content=body["content"],
                idempotency_key=fresh_idempotency_key(),
                idempotency_body_hash=body_hash(body),
            )
            twist_ids.append(r.twist.id)

        async with session_factory() as s:
            await TwistsRepo(s).soft_delete(twist_ids[0])
            await s.commit()

        # A 4th submit must still raise OverQuota — delete didn't free a slot.
        body4 = {"chapter_id": str(chapter_public_id), "content": "deberia fallar"}
        with pytest.raises(OverQuota):
            await service.submit(
                user_id=user[0],
                chapter_public_id=chapter_public_id,
                content=body4["content"],
                idempotency_key=fresh_idempotency_key(),
                idempotency_body_hash=body_hash(body4),
            )

        # DB: 3 rows total (2 pending_review + 1 deleted_by_user).
        row = await setup_session.execute(
            sa.text(
                "SELECT COUNT(*) FROM twists "
                "WHERE user_id = :uid AND chapter_id = :cid"
            ),
            {"uid": user[0], "cid": chapter_id},
        )
        assert int(row.scalar_one()) == 3
    finally:
        await cleanup(setup_session, season_id, user)


async def test_quota_isolated_per_user(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "iso-user-001"
    )
    u1 = await make_user(setup_session)
    u2 = await make_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    try:
        for i in range(3):
            body = {"chapter_id": str(chapter_public_id), "content": f"u1 idea {i} xxx"}
            await service.submit(
                user_id=u1[0],
                chapter_public_id=chapter_public_id,
                content=body["content"],
                idempotency_key=fresh_idempotency_key(),
                idempotency_body_hash=body_hash(body),
            )
        # u1 at cap. u2 must still be allowed.
        body_u2 = {"chapter_id": str(chapter_public_id), "content": "u2 primera xxx"}
        result = await service.submit(
            user_id=u2[0],
            chapter_public_id=chapter_public_id,
            content=body_u2["content"],
            idempotency_key=fresh_idempotency_key(),
            idempotency_body_hash=body_hash(body_u2),
        )
        assert result.quota.used == 1
    finally:
        await cleanup(setup_session, season_id, u1, u2)


async def test_chapter_mismatch_raises(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A submit with a stale or wrong chapter_public_id is rejected."""
    season_id, _, chapter_public_id = await make_active_recepcion_setup(
        setup_session, "mismatch-001"
    )
    user = await make_user(setup_session)
    await setup_session.commit()
    clear_flags_cache()

    service = _service(session_factory)
    wrong_chapter = uuid4()
    body = {"chapter_id": str(wrong_chapter), "content": "chapter equivocado xxx"}
    try:
        with pytest.raises(ChapterMismatch):
            await service.submit(
                user_id=user[0],
                chapter_public_id=wrong_chapter,
                content=body["content"],
                idempotency_key=fresh_idempotency_key(),
                idempotency_body_hash=body_hash(body),
            )
        # Sanity: the live chapter exists, we just used a different id.
        assert chapter_public_id != wrong_chapter
    finally:
        await cleanup(setup_session, season_id, user)
