"""TwistSubmissionService — orchestrate user twist submissions.

Module 005 / Task T-005.

The single mutation entry point for the RECEPCION_IDEAS window. Combines:
  - kill-switch + cycle state gate (reuses module 003 + 004 helpers)
  - Idempotency-Key replay (via :class:`IdempotencyRepo`)
  - Window deadline check (via module 004's ``compute_windows``)
  - Per-user-per-chapter advisory lock + recount + INSERT
    (via :class:`TwistsRepo`)
  - Pure content normalization (via ``twist_content.normalize``)
  - Pure quota value object (:class:`QuotaState`)

Transport-agnostic: raises typed exceptions and returns a dataclass. The
HTTP layer (T-007) maps exceptions to RFC 7807 ``Problem`` responses per
the spec's FR-002 validation order.

Exception → HTTP mapping (for the handler's reference):
  - :class:`KillSwitchActive`    → 503 ``under_maintenance``
  - :class:`WindowClosed`        → 409 ``window_closed``
  - :class:`ChapterMismatch`     → 409 ``chapter_mismatch``
  - :class:`OverQuota`           → 409 ``over_quota``
  - :class:`IdempotencyConflict` → 409 ``idempotency_conflict``
  - :class:`TwistLockBusy`       → 503 ``lock_busy``
  - ``ValueError`` from ``twist_content.normalize`` → 422 ``validation_error``

The mutation transaction follows the SQL outline in
``data-model.md §Submission transaction``: kill-switch + idempotency +
chapter resolution + window check happen before the transaction; lock +
recount + INSERT + idempotency cache happen inside one transaction so
they share commit/rollback semantics.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.cycle_executor import KillSwitchActive
from app.domain.twist_content import normalize as normalize_content
from app.domain.twist_quota import QuotaState
from app.domain.windows import CycleTimes, compute_windows
from app.infra.characters_repo import CharactersRepo
from app.infra.content_repo import ContentRepo
from app.infra.idempotency_repo import IdempotencyRecord, IdempotencyRepo
from app.infra.system_flags_repo import SystemFlagsRepo
from app.infra.twists_repo import Twist, TwistLockBusy, TwistsRepo

__all__ = [
    "AlreadyFiltered",
    "ChapterMismatch",
    "DeleteResult",
    "ForbiddenNotOwner",
    "IdempotencyConflict",
    "InvalidCharacter",
    "KillSwitchActive",
    "ListMineResult",
    "OverQuota",
    "SubmitResult",
    "TwistLockBusy",
    "TwistNotFound",
    "TwistSubmissionService",
    "WindowClosed",
]


# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class WindowClosed(Exception):
    """The submit window is closed or no live chapter is available.

    Mapped to 409 ``window_closed`` by the HTTP layer.
    """


class OverQuota(Exception):
    """User has used their per-chapter quota.

    Mapped to 409 ``over_quota`` with ``quota_used`` and ``quota_max``.
    """

    def __init__(self, used: int, max_: int) -> None:
        self.used = used
        self.max = max_
        super().__init__(
            f"Twist quota exhausted: used {used}/{max_}"
        )


class ChapterMismatch(Exception):
    """Provided ``chapter_public_id`` is not the current live chapter.

    Mapped to 409 ``chapter_mismatch``.
    """


class IdempotencyConflict(Exception):
    """Same ``Idempotency-Key`` was reused with a different body hash.

    Mapped to 409 ``idempotency_conflict``.
    """


class TwistNotFound(Exception):
    """No twist exists with the given ``public_id``.

    Mapped to 404 ``twist_not_found``.
    """


class ForbiddenNotOwner(Exception):
    """The twist exists but belongs to another user.

    Mapped to 403 ``forbidden_not_owner`` (R-006: 403 not 404 — UUIDv4
    public_ids are not enumerable, so honesty wins over obscurity).
    """


class AlreadyFiltered(Exception):
    """The twist has already been processed by the director filter.

    Status moved past ``pending_review``/``deleted_by_user`` (i.e. it is
    now ``approved`` or ``rejected_*``), so it is immutable. Mapped to
    409 ``already_filtered``.
    """


class InvalidCharacter(Exception):
    """The submitted ``character_id`` does not point at an active character.

    Either the id does not exist or the character is hidden
    (``active=FALSE``). Mapped to 422 ``invalid_character`` by the HTTP
    layer. Raised **before** quota consumption — a rejected submission
    for an invalid character does NOT burn the user's quota.
    """

    def __init__(self, character_id: int) -> None:
        self.character_id = character_id
        super().__init__(f"character_id={character_id} is not active")


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubmitResult:
    """Outcome of a ``submit()`` call.

    Attributes
    ----------
    twist:
        The persisted (or replayed-from-cache) twist row.
    quota:
        Post-insert quota snapshot for the (user, chapter) pair.
    was_replay:
        ``True`` when the call returned a cached response because the
        Idempotency-Key matched a prior request. ``False`` for fresh
        inserts. The HTTP layer uses this to pick 200 vs 201.
    """

    twist: Twist
    quota: QuotaState
    was_replay: bool


@dataclass(frozen=True)
class DeleteResult:
    """Outcome of a ``delete()`` call.

    Attributes
    ----------
    deleted_at:
        The persisted soft-delete timestamp. On idempotent replay, this
        is the original timestamp from the first delete (not now()).
    quota:
        Post-delete quota snapshot. ``used`` is unchanged from before the
        delete since FR-004 says deletes do NOT free quota.
    was_idempotent:
        ``True`` when the twist was already ``deleted_by_user`` before
        this call. The HTTP layer can still return 200 either way.
    """

    deleted_at: datetime
    quota: QuotaState
    was_idempotent: bool


@dataclass(frozen=True)
class ListMineResult:
    """Outcome of a ``list_mine()`` call.

    Attributes
    ----------
    items:
        The user's twists for the currently live chapter, ordered by
        ``submitted_at`` ASC. Includes ``deleted_by_user`` rows so the
        ``/me/twists`` UI can show the full history.
    quota:
        Snapshot derived from the list length (used = len(items)).
    """

    items: list[Twist]
    quota: QuotaState


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(UTC)


class TwistSubmissionService:
    """Orchestrate ``POST /api/v1/twists/submit`` calls.

    Parameters
    ----------
    session_factory:
        Source of new :class:`AsyncSession` instances. The service opens
        a fresh session per ``submit()`` call so the mutation transaction
        is fully self-contained.
    cycle_times:
        ART times-of-day for ESTRENO / FILTERING / GENERACION (module 004
        T-002). Used to compute ``submit_until`` for the window gate.
    max_per_chapter:
        Quota cap from ``settings.max_twists_per_user_per_chapter``
        (default 3).
    now_utc:
        Clock source. Defaults to ``datetime.now(UTC)``. Injectable for
        deterministic tests.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        cycle_times: CycleTimes,
        max_per_chapter: int,
        now_utc: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._session_factory = session_factory
        self._cycle_times = cycle_times
        self._max = max_per_chapter
        self._now = now_utc

    # -----------------------------------------------------------------------
    # Public entry point
    # -----------------------------------------------------------------------

    async def submit(
        self,
        user_id: int,
        chapter_public_id: UUID,
        content: str,
        idempotency_key: str,
        idempotency_body_hash: str,
        character_id: int | None = None,
    ) -> SubmitResult:
        """Submit a twist proposal for the currently-live chapter.

        ``character_id`` (Ronda 7 / module 013) is the catalog id chosen
        by the user. The HTTP layer always supplies an explicit value;
        the service treats ``None`` as "no character preference" and lets
        the repo's INSERT fall back to the lowest-``sort_order`` active
        character. This keeps legacy tests (written before the FK) working
        unmodified; production code always passes the explicit id.

        Raises
        ------
        KillSwitchActive
            ``kill_switch.on = TRUE``.
        WindowClosed
            No active season, no live chapter, cycle not in
            ``RECEPCION_IDEAS``, or ``now >= submit_until``.
        ChapterMismatch
            ``chapter_public_id`` does not match the currently live chapter.
        InvalidCharacter
            ``character_id`` was supplied but is unknown or inactive.
            Mapped to 422 by the HTTP layer. Raised before quota
            consumption. Never raised when ``character_id is None``.
        OverQuota
            User has used the per-chapter quota (deleted twists count too,
            FR-004).
        IdempotencyConflict
            ``idempotency_key`` was reused with a different body hash.
        TwistLockBusy
            The per-(user, chapter) advisory lock could not be acquired
            within 1 s.
        ValueError
            Raised by ``twist_content.normalize`` when ``content`` is out
            of bounds. The HTTP layer maps this to 422.
        """
        async with self._session_factory() as session, session.begin():
            flags_repo = SystemFlagsRepo(session)
            idem_repo = IdempotencyRepo(session)
            content_repo = ContentRepo(session)
            twists_repo = TwistsRepo(session)
            chars_repo = CharactersRepo(session)

            # 1. Kill-switch (cached 30 s).
            await self._ensure_not_killed(flags_repo)

            # 2. Idempotency replay check (key is immutable once written;
            # no extra lock needed beyond the surrounding transaction).
            existing = await idem_repo.get(idempotency_key)
            if existing is not None:
                if existing.request_hash != idempotency_body_hash:
                    raise IdempotencyConflict
                return self._replay_from_cache(existing)

            # 3-4. Resolve active cycle + chapter + window deadline.
            payload = await content_repo.get_today_payload()
            if payload is None or payload.chapter_status != "live":
                raise WindowClosed
            if payload.chapter_public_id != chapter_public_id:
                raise ChapterMismatch

            windows = compute_windows(
                cycle_state=payload.cycle_state,
                state_entered_at=payload.cycle_state_entered_at,
                cycle_date=payload.cycle_date,
                now_utc=self._now(),
                cycle_times=self._cycle_times,
            )
            now = self._now()
            if (
                payload.cycle_state != "RECEPCION_IDEAS"
                or now >= windows.submit_until
            ):
                raise WindowClosed

            chapter_id = payload.chapter_id

            # 5. Normalize content (raises ValueError → HTTP 422).
            normalized = normalize_content(content)

            # 5b. Character must exist and be active when supplied
            # (raises InvalidCharacter → HTTP 422). Done before quota so a
            # rejected submission does not burn the user's quota. When
            # the caller passes ``None`` the repo falls back to the
            # lowest-sort_order active character (legacy-test path).
            if (
                character_id is not None
                and await chars_repo.get_by_id_if_active(character_id) is None
            ):
                raise InvalidCharacter(character_id)

            # 6. Mutation: lock → recount → INSERT → idem-cache.
            # The advisory lock + counts + writes share commit/rollback
            # semantics with the pre-check queries above.
            await twists_repo.lock_user_chapter(user_id, chapter_id)

            used = await twists_repo.count_for_user_chapter(
                user_id, chapter_id
            )
            if used >= self._max:
                # The `session.begin()` context manager rolls back on
                # exception, releasing the advisory lock.
                raise OverQuota(used, self._max)

            twist = await twists_repo.insert(
                chapter_id=chapter_id,
                user_id=user_id,
                content=normalized,
                character_id=character_id,
            )

            response_payload = _build_response_payload(
                twist, used + 1, self._max
            )
            await idem_repo.insert(
                key=idempotency_key,
                user_id=user_id,
                request_hash=idempotency_body_hash,
                response_json=response_payload,
            )
        # Transaction commits on exit of `session.begin()`.

        return SubmitResult(
            twist=twist,
            quota=QuotaState(used=used + 1, max=self._max),
            was_replay=False,
        )

    # -----------------------------------------------------------------------
    # Public entry points — delete + list_mine
    # -----------------------------------------------------------------------

    async def delete(
        self,
        user_id: int,
        twist_public_id: UUID,
    ) -> DeleteResult:
        """Soft-delete a pending twist.

        Idempotent: re-delete of an already-deleted twist returns the
        original ``deleted_at``. Per FR-004, the user's quota is NOT
        freed.

        Raises
        ------
        KillSwitchActive
        WindowClosed
            No active season / no live chapter / cycle not in
            ``RECEPCION_IDEAS`` / ``now >= submit_until``.
        TwistNotFound
            ``twist_public_id`` is unknown.
        ForbiddenNotOwner
            The twist belongs to another user.
        AlreadyFiltered
            Status moved past ``pending_review``/``deleted_by_user`` —
            the director filter already processed it.
        """
        async with self._session_factory() as session, session.begin():
            flags_repo = SystemFlagsRepo(session)
            content_repo = ContentRepo(session)
            twists_repo = TwistsRepo(session)

            # 1. Kill-switch.
            await self._ensure_not_killed(flags_repo)

            # 2. Window gate: same rules as submit (RECEPCION_IDEAS + deadline).
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
                payload.cycle_state != "RECEPCION_IDEAS"
                or self._now() >= windows.submit_until
            ):
                raise WindowClosed

            # 3. Lock the twist row (PK index → cheap).
            twist = await twists_repo.get_by_public_id_for_update(
                twist_public_id
            )
            if twist is None:
                raise TwistNotFound
            if twist.user_id != user_id:
                raise ForbiddenNotOwner

            # 4. Status gate.
            if twist.status not in ("pending_review", "deleted_by_user"):
                raise AlreadyFiltered

            # 5. Idempotency: re-delete is a no-op returning original timestamp.
            if twist.status == "deleted_by_user":
                assert twist.deleted_at is not None, (
                    "ck_twists_deleted_consistency invariant violated"
                )
                used = await twists_repo.count_for_user_chapter(
                    user_id, twist.chapter_id
                )
                return DeleteResult(
                    deleted_at=twist.deleted_at,
                    quota=QuotaState(used=used, max=self._max),
                    was_idempotent=True,
                )

            # 6. Soft delete.
            deleted_at = await twists_repo.soft_delete(twist.id)

            # 7. Recompute quota for the response. Delete does NOT free
            # (FR-004), so `used` stays the same.
            used = await twists_repo.count_for_user_chapter(
                user_id, twist.chapter_id
            )

        return DeleteResult(
            deleted_at=deleted_at,
            quota=QuotaState(used=used, max=self._max),
            was_idempotent=False,
        )

    async def list_mine(self, user_id: int) -> ListMineResult:
        """Return the user's twists for the currently live chapter.

        Includes ``deleted_by_user`` rows so the PWA can show the full
        history. Items are ordered by ``submitted_at`` ASC.

        If there is no active season or no live chapter, returns an
        empty list with ``quota=QuotaState(used=0, max=...)`` rather
        than raising — a benign read should not penalize the client.

        Raises
        ------
        KillSwitchActive
        """
        async with self._session_factory() as session:
            flags_repo = SystemFlagsRepo(session)
            content_repo = ContentRepo(session)
            twists_repo = TwistsRepo(session)

            await self._ensure_not_killed(flags_repo)

            payload = await content_repo.get_today_payload()
            if payload is None or payload.chapter_status != "live":
                return ListMineResult(
                    items=[],
                    quota=QuotaState(used=0, max=self._max),
                )

            # `limit = max + 1` is defensive: under the advisory lock we
            # should never exceed `max` rows, but if a bug ever lets us
            # over-quota, surface it instead of silently truncating.
            items = await twists_repo.list_for_user_chapter(
                user_id=user_id,
                chapter_id=payload.chapter_id,
                limit=self._max + 1,
            )

        return ListMineResult(
            items=items,
            quota=QuotaState(used=len(items), max=self._max),
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

    def _replay_from_cache(self, record: IdempotencyRecord) -> SubmitResult:
        """Rebuild :class:`SubmitResult` from a cached idempotency record."""
        cached = record.response_json
        twist = _twist_from_cached(cached["twist"])
        quota_used = int(cached["quota_used"])
        return SubmitResult(
            twist=twist,
            quota=QuotaState(used=quota_used, max=self._max),
            was_replay=True,
        )


# ---------------------------------------------------------------------------
# Cached response payload helpers
# ---------------------------------------------------------------------------


def _build_response_payload(
    twist: Twist, quota_used: int, quota_max: int
) -> dict[str, Any]:
    """Build the response JSON to cache for future idempotency replays.

    Shape matches what the HTTP handler will return (T-007). Keeping it
    here makes the service the authority on the wire contract.
    """
    return {
        "twist": {
            "id": int(twist.id),
            "public_id": str(twist.public_id),
            "chapter_id": int(twist.chapter_id),
            "user_id": int(twist.user_id),
            "content": twist.content,
            "status": twist.status,
            "submitted_at": twist.submitted_at.isoformat(),
            "character_id": int(twist.character_id),
        },
        "quota_used": quota_used,
        "quota_max": quota_max,
    }


def _twist_from_cached(cached: dict[str, Any]) -> Twist:
    """Reconstruct a :class:`Twist` dataclass from a cached JSON payload."""
    return Twist(
        id=int(cached["id"]),
        public_id=UUID(str(cached["public_id"])),
        chapter_id=int(cached["chapter_id"]),
        user_id=int(cached["user_id"]),
        content=str(cached["content"]),
        status=str(cached["status"]),
        director_reason=None,
        submitted_at=datetime.fromisoformat(str(cached["submitted_at"])),
        reviewed_at=None,
        deleted_at=None,
        character_id=int(cached["character_id"]),
    )
