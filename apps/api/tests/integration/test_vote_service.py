"""Integration tests: VoteService.cast() + window enforcement.

Module 007 / Task T-005.

Covers the five spec scenarios:
  - happy path (1 vote, returns CastResult with quota.used=1, new_vote_count=1).
  - double-tap (second cast for same twist raises AlreadyVoted).
  - over-quota (Nth vote where N > max raises OverQuota).
  - self-vote allowed by default; rejected when allow_self_vote=False.
  - window closed (cycle != VOTACION or now >= vote_until → WindowClosed).
"""
# ruff: noqa: F811 — pytest fixtures are re-imported by name for collection.

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.vote_service import (
    AlreadyVoted,
    CannotSelfVote,
    OverQuota,
    VoteService,
    WindowClosed,
)
from app.domain.windows import CycleTimes

from ._vote_service_helpers import (
    NOW_AFTER_WINDOW,
    NOW_IN_WINDOW,
    _ensure_migrated,  # noqa: F401 — re-exported autouse fixture
    cleanup,
    database_url,  # noqa: F401
    make_active_votacion_setup,
    make_approved_twist,
    make_user,
    session_factory,  # noqa: F401
    setup_session,  # noqa: F401
)


def _build_service(
    session_factory_: async_sessionmaker[AsyncSession],
    *,
    max_per_chapter: int = 5,
    allow_self_vote: bool = True,
    now: object = NOW_IN_WINDOW,
) -> VoteService:
    return VoteService(
        session_factory=session_factory_,
        cycle_times=CycleTimes.default(),
        max_per_chapter=max_per_chapter,
        allow_self_vote=allow_self_vote,
        now_utc=lambda: now,  # type: ignore[arg-type, return-value]
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_cast_happy_path_returns_quota_and_count(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """One vote → CastResult with new_vote_count=1, quota=(1, 5)."""
    season_id, chapter_id, _ = await make_active_votacion_setup(
        setup_session, "happy"
    )
    voter = await make_user(setup_session)
    author = await make_user(setup_session)
    _, twist_public = await make_approved_twist(
        setup_session, chapter_id, author[0], "una idea aprobada xx"
    )
    await setup_session.commit()

    service = _build_service(session_factory)
    try:
        result = await service.cast(voter[0], twist_public)
        assert result.twist_public_id == twist_public
        assert result.new_vote_count == 1
        assert result.quota.used == 1
        assert result.quota.max == 5
        assert result.quota.remaining == 4
    finally:
        await cleanup(setup_session, season_id, voter, author)


# ---------------------------------------------------------------------------
# Double-tap → AlreadyVoted
# ---------------------------------------------------------------------------


async def test_cast_double_tap_raises_already_voted(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    season_id, chapter_id, _ = await make_active_votacion_setup(
        setup_session, "dbl"
    )
    voter = await make_user(setup_session)
    author = await make_user(setup_session)
    _, twist_public = await make_approved_twist(
        setup_session, chapter_id, author[0], "idea repetida xxx"
    )
    await setup_session.commit()

    service = _build_service(session_factory)
    try:
        await service.cast(voter[0], twist_public)
        with pytest.raises(AlreadyVoted):
            await service.cast(voter[0], twist_public)
    finally:
        await cleanup(setup_session, season_id, voter, author)


# ---------------------------------------------------------------------------
# Over-quota
# ---------------------------------------------------------------------------


async def test_cast_over_quota_raises(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """max=2, vote on 2 twists OK, 3rd raises OverQuota."""
    season_id, chapter_id, _ = await make_active_votacion_setup(
        setup_session, "quota"
    )
    voter = await make_user(setup_session)
    author = await make_user(setup_session)
    twists = []
    for i in range(3):
        _, public = await make_approved_twist(
            setup_session, chapter_id, author[0], f"twist numero {i} xxxx"
        )
        twists.append(public)
    await setup_session.commit()

    service = _build_service(session_factory, max_per_chapter=2)
    try:
        await service.cast(voter[0], twists[0])
        await service.cast(voter[0], twists[1])
        with pytest.raises(OverQuota) as exc:
            await service.cast(voter[0], twists[2])
        assert exc.value.used == 2
        assert exc.value.max == 2
    finally:
        await cleanup(setup_session, season_id, voter, author)


# ---------------------------------------------------------------------------
# Self-vote
# ---------------------------------------------------------------------------


async def test_self_vote_allowed_by_default(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """``allow_self_vote=True`` (default) → voting for your own twist succeeds."""
    season_id, chapter_id, _ = await make_active_votacion_setup(
        setup_session, "self-yes"
    )
    user = await make_user(setup_session)
    _, twist_public = await make_approved_twist(
        setup_session, chapter_id, user[0], "mi propia idea xx"
    )
    await setup_session.commit()

    service = _build_service(session_factory, allow_self_vote=True)
    try:
        result = await service.cast(user[0], twist_public)
        assert result.new_vote_count == 1
    finally:
        await cleanup(setup_session, season_id, user)


async def test_self_vote_rejected_when_disabled(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """``allow_self_vote=False`` → CannotSelfVote on the author's own twist."""
    season_id, chapter_id, _ = await make_active_votacion_setup(
        setup_session, "self-no"
    )
    user = await make_user(setup_session)
    _, twist_public = await make_approved_twist(
        setup_session, chapter_id, user[0], "mia tambien xxx"
    )
    await setup_session.commit()

    service = _build_service(session_factory, allow_self_vote=False)
    try:
        with pytest.raises(CannotSelfVote):
            await service.cast(user[0], twist_public)
    finally:
        await cleanup(setup_session, season_id, user)


# ---------------------------------------------------------------------------
# Window closed
# ---------------------------------------------------------------------------


async def test_cast_after_vote_until_raises_window_closed(
    setup_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """now >= vote_until → WindowClosed even when cycle.state == VOTACION."""
    season_id, chapter_id, _ = await make_active_votacion_setup(
        setup_session, "win-late"
    )
    voter = await make_user(setup_session)
    author = await make_user(setup_session)
    _, twist_public = await make_approved_twist(
        setup_session, chapter_id, author[0], "tardia xxxxx xxxx"
    )
    await setup_session.commit()

    # Inject a "now" past the vote_until.
    service = _build_service(session_factory, now=NOW_AFTER_WINDOW)
    try:
        with pytest.raises(WindowClosed):
            await service.cast(voter[0], twist_public)
    finally:
        await cleanup(setup_session, season_id, voter, author)
