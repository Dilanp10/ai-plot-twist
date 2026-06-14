"""VoteService — orchestrate vote-feed reads and vote-cast writes.

Module 007 / Task T-005.

Combines:
  - kill-switch + cycle state gate (reuses module 003 + 004 helpers)
  - VOTACION window check (via module 004's ``compute_windows``)
  - per-user stable sort + cursor pagination (T-002 + T-003)
  - per-(user, chapter) advisory lock + recount + INSERT
    (via :class:`VotesRepo`)
  - ``ON CONFLICT (twist_id, user_id) DO NOTHING`` as the natural
    idempotency anchor for the same-twist double-tap

Transport-agnostic: raises typed exceptions and returns dataclasses.
The HTTP layer (T-006 / T-007) maps exceptions to RFC 7807 ``Problem``
responses per the contract.

Exception → HTTP mapping (for the handler's reference):
  - :class:`KillSwitchActive`    → 503 ``under_maintenance``
  - :class:`WindowClosed`        → 409 ``window_closed``
  - :class:`TwistNotVotable`     → 409 ``twist_not_votable``
  - :class:`ChapterMismatch`     → 409 ``chapter_mismatch``
  - :class:`CannotSelfVote`      → 409 ``cannot_self_vote``
  - :class:`OverQuota`           → 409 ``over_quota``
  - :class:`AlreadyVoted`        → 409 ``already_voted``
  - :class:`VoteLockBusy`        → 503 ``lock_busy``
  - :class:`CursorInvalid`       → 422 ``cursor_invalid``
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.cycle_executor import KillSwitchActive
from app.domain.vote_cursor import Cursor, CursorInvalid
from app.domain.vote_cursor import decode as decode_cursor
from app.domain.vote_cursor import encode as encode_cursor
from app.domain.vote_sort import shuffle_stable, sort_hot, sort_recent
from app.domain.windows import CycleTimes, compute_windows
from app.infra.content_repo import ContentRepo
from app.infra.system_flags_repo import SystemFlagsRepo
from app.infra.twists_repo import TwistsRepo
from app.infra.votes_repo import FeedRow, VoteLockBusy, VotesRepo

__all__ = [
    "AlreadyVoted",
    "CannotSelfVote",
    "CastResult",
    "ChapterMismatch",
    "CursorInvalid",
    "FeedItem",
    "FeedResult",
    "KillSwitchActive",
    "OverQuota",
    "PageInfo",
    "QuotaSnapshot",
    "TwistNotVotable",
    "VoteLockBusy",
    "VoteService",
    "WindowClosed",
]


# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class WindowClosed(Exception):
    """Voting is not open: no live chapter, cycle != VOTACION, or now ≥ vote_until.

    Mapped to 409 ``window_closed``.
    """


class TwistNotVotable(Exception):
    """The twist either doesn't exist or is not in status ``approved``.

    Triggered by:
      - unknown ``twist_public_id``,
      - ``status`` in ``pending_review`` / ``rejected_*`` / ``deleted_by_user``.

    Mapped to 409 ``twist_not_votable``.
    """


class ChapterMismatch(Exception):
    """The twist belongs to a different chapter than the currently-live one.

    Should not happen via the PWA (which only shows current-chapter twists),
    but defensive against client-constructed requests with stale ids.

    Mapped to 409 ``chapter_mismatch``.
    """


class CannotSelfVote(Exception):
    """``ALLOW_SELF_VOTE=false`` and the user is the twist's author.

    Mapped to 409 ``cannot_self_vote``.
    """


class OverQuota(Exception):
    """User has cast ``max_votes_per_user_per_chapter`` votes already.

    Mapped to 409 ``over_quota`` with ``quota_used`` and ``quota_max``.
    """

    def __init__(self, used: int, max_: int) -> None:
        self.used = used
        self.max = max_
        super().__init__(f"Vote quota exhausted: used {used}/{max_}")


class AlreadyVoted(Exception):
    """A vote for this (twist, user) already exists.

    Mapped to 409 ``already_voted``. Surfaced when the
    ``INSERT … ON CONFLICT DO NOTHING`` affects 0 rows.
    """


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QuotaSnapshot:
    """Immutable (used, max) pair with a clamped ``remaining`` projection.

    Distinct from :class:`app.domain.twist_quota.QuotaState` so changes to
    vote-quota wire format don't accidentally ripple into twists.
    """

    used: int
    max: int

    @property
    def remaining(self) -> int:
        diff = self.max - self.used
        return diff if diff > 0 else 0


@dataclass(frozen=True)
class CastResult:
    """Outcome of a successful :meth:`VoteService.cast` call."""

    twist_public_id: UUID
    new_vote_count: int
    quota: QuotaSnapshot


@dataclass(frozen=True)
class FeedItem:
    """One row of the vote-feed response."""

    public_id: UUID
    content: str
    vote_count: int
    has_my_vote: bool


@dataclass(frozen=True)
class PageInfo:
    """Pagination metadata for the vote-feed response."""

    next_cursor: str | None
    limit: int
    total_approved: int


@dataclass(frozen=True)
class FeedResult:
    """Full :meth:`VoteService.feed` payload."""

    items: list[FeedItem]
    page: PageInfo
    quota: QuotaSnapshot


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(UTC)


_VALID_SORTS = ("random", "recent", "hot")


class VoteService:
    """Orchestrate vote-feed reads and vote-cast writes.

    Parameters
    ----------
    session_factory:
        Source of new :class:`AsyncSession` instances. The service opens
        a fresh session per call so the mutation transaction is fully
        self-contained.
    cycle_times:
        ART times-of-day for ESTRENO / FILTERING / GENERACION (module 004
        T-002). Used to compute ``vote_until`` for the window gate.
    max_per_chapter:
        Quota cap from ``settings.max_votes_per_user_per_chapter``
        (default 5).
    allow_self_vote:
        From ``settings.allow_self_vote`` (default True).
    now_utc:
        Clock source. Defaults to ``datetime.now(UTC)``. Injectable for
        deterministic tests.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        cycle_times: CycleTimes,
        max_per_chapter: int,
        allow_self_vote: bool,
        now_utc: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._session_factory = session_factory
        self._cycle_times = cycle_times
        self._max = max_per_chapter
        self._allow_self_vote = allow_self_vote
        self._now = now_utc

    # -----------------------------------------------------------------------
    # cast()
    # -----------------------------------------------------------------------

    async def cast(
        self, user_id: int, twist_public_id: UUID
    ) -> CastResult:
        """Cast a vote for the given twist.

        Raises
        ------
        KillSwitchActive
        WindowClosed
            No live chapter, ``cycle.state != VOTACION``, or
            ``now >= vote_until``.
        TwistNotVotable
            Unknown twist or status not ``approved``.
        ChapterMismatch
            Twist belongs to a different chapter.
        CannotSelfVote
            ``allow_self_vote=False`` and the user is the author.
        OverQuota
            User has reached ``max_per_chapter``.
        AlreadyVoted
            UNIQUE constraint absorbed the insert.
        VoteLockBusy
            Per-(user, chapter) advisory lock could not be acquired within 1 s.
        """
        async with self._session_factory() as session, session.begin():
            flags_repo = SystemFlagsRepo(session)
            content_repo = ContentRepo(session)
            twists_repo = TwistsRepo(session)
            votes_repo = VotesRepo(session)

            # 1. Kill-switch.
            await self._ensure_not_killed(flags_repo)

            # 2. Window gate.
            payload = await self._require_open_window(content_repo)

            # 3. Resolve twist + verify status + chapter.
            twist = await twists_repo.get_by_public_id_for_update(
                twist_public_id
            )
            if twist is None or twist.status != "approved":
                raise TwistNotVotable
            if twist.chapter_id != payload.chapter_id:
                raise ChapterMismatch

            # 4. Self-vote gate.
            if not self._allow_self_vote and twist.user_id == user_id:
                raise CannotSelfVote

            chapter_id = payload.chapter_id

            # 5. Lock → recount → INSERT.
            await votes_repo.lock_user_chapter(user_id, chapter_id)

            used = await votes_repo.count_for_user_chapter(
                user_id, chapter_id
            )
            if used >= self._max:
                raise OverQuota(used, self._max)

            new_id = await votes_repo.vote_atomic(
                twist_id=twist.id,
                user_id=user_id,
                chapter_id=chapter_id,
            )
            if new_id is None:
                # UNIQUE absorbed the insert: the user already voted for this twist.
                raise AlreadyVoted

            # 6. Compute response counts. ``count_for_twist`` runs after the
            # insert is visible within this transaction.
            new_vote_count = await votes_repo.count_for_twist(twist.id)
        # Transaction commits on context exit.

        return CastResult(
            twist_public_id=twist_public_id,
            new_vote_count=new_vote_count,
            quota=QuotaSnapshot(used=used + 1, max=self._max),
        )

    # -----------------------------------------------------------------------
    # feed()
    # -----------------------------------------------------------------------

    async def feed(
        self,
        user_id: int,
        sort: str,
        limit: int,
        cursor: str | None,
    ) -> FeedResult:
        """Return the user's vote-feed page.

        ``sort`` must be one of ``random`` / ``recent`` / ``hot`` (FR-002).
        ``cursor`` is opaque; produced by a prior call.

        Raises
        ------
        KillSwitchActive
        WindowClosed
        CursorInvalid
            Malformed cursor, or the cursor's sort doesn't match ``sort``.
        ValueError
            ``sort`` not in the valid set (the handler should validate first
            and return 422, but defensive).
        """
        if sort not in _VALID_SORTS:
            raise ValueError(f"Unknown sort: {sort!r}")

        decoded_cursor: Cursor | None = None
        if cursor is not None:
            decoded_cursor = decode_cursor(cursor)
            if decoded_cursor.sort != sort:
                raise CursorInvalid(
                    f"cursor.sort={decoded_cursor.sort!r} does not match "
                    f"query.sort={sort!r}"
                )

        async with self._session_factory() as session:
            flags_repo = SystemFlagsRepo(session)
            content_repo = ContentRepo(session)
            votes_repo = VotesRepo(session)

            await self._ensure_not_killed(flags_repo)
            payload = await self._require_open_window(content_repo)

            rows = await votes_repo.list_approved_with_vote_counts(
                payload.chapter_id
            )
            my_votes = await votes_repo.list_for_user_chapter(
                user_id=user_id, chapter_id=payload.chapter_id
            )

        my_voted_twist_ids: set[int] = {v.twist_id for v in my_votes}
        total_approved = len(rows)
        used = len(my_votes)

        sorted_rows = self._apply_sort(
            rows, sort=sort, cycle_id=payload.cycle_id, user_id=user_id
        )

        start = decoded_cursor.last_position if decoded_cursor is not None else 0
        end = start + limit
        page_rows = sorted_rows[start:end]

        items = [
            FeedItem(
                public_id=row.public_id,
                content=row.content,
                vote_count=row.vote_count,
                has_my_vote=row.id in my_voted_twist_ids,
            )
            for row in page_rows
        ]

        next_cursor: str | None = None
        if end < total_approved:
            next_cursor = encode_cursor(
                Cursor(
                    sort=_as_sort_literal(sort),
                    last_position=end,
                    last_sort_value=_last_sort_value(sort, page_rows[-1])
                    if page_rows
                    else None,
                )
            )

        return FeedResult(
            items=items,
            page=PageInfo(
                next_cursor=next_cursor,
                limit=limit,
                total_approved=total_approved,
            ),
            quota=QuotaSnapshot(used=used, max=self._max),
        )

    # -----------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------

    async def _ensure_not_killed(self, flags_repo: SystemFlagsRepo) -> None:
        if await flags_repo.is_kill_switch_on():
            flag = await flags_repo.get("kill_switch")
            reason: str | None = None
            if flag is not None:
                raw_reason = flag.flag_value.get("reason")
                reason = str(raw_reason) if raw_reason is not None else None
            raise KillSwitchActive(reason)

    async def _require_open_window(self, content_repo: ContentRepo):  # type: ignore[no-untyped-def]
        """Verify VOTACION window is open; return the TodayPayload."""
        payload = await content_repo.get_today_payload()
        if payload is None or payload.chapter_status != "live":
            raise WindowClosed
        windows = compute_windows(
            cycle_state=payload.cycle_state,
            state_entered_at=payload.cycle_state_entered_at,
            cycle_date=payload.cycle_date,
            now_utc=self._now(),
            cycle_times=self._cycle_times,
        )
        if (
            payload.cycle_state != "VOTACION"
            or self._now() >= windows.vote_until
        ):
            raise WindowClosed
        return payload

    def _apply_sort(
        self,
        rows: list[FeedRow],
        *,
        sort: str,
        cycle_id: int,
        user_id: int,
    ) -> list[FeedRow]:
        if sort == "random":
            return shuffle_stable(
                rows, cycle_id=cycle_id, user_id=user_id
            )
        if sort == "recent":
            return sort_recent(rows)
        if sort == "hot":
            return sort_hot(rows)
        raise ValueError(f"Unknown sort: {sort!r}")


def _as_sort_literal(sort: str):  # type: ignore[no-untyped-def]
    """Narrow ``str`` to the ``Sort`` literal for :func:`encode_cursor`."""
    if sort not in _VALID_SORTS:
        raise ValueError(f"Unknown sort: {sort!r}")
    return sort


def _last_sort_value(sort: str, row: FeedRow) -> int | str | None:
    """Project the secondary cursor key for the last row in the page.

    Used purely as a sanity hint for downstream calls; the server treats
    ``last_position`` as the authoritative offset.
    """
    if sort == "recent":
        return row.submitted_at.isoformat()
    if sort == "hot":
        return row.vote_count
    return None
