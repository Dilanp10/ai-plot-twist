"""TwistsRepo — SQLAlchemy Core repository for the ``twists`` table.

Module 005 / Task T-004 (base methods).
Module 006 / Task T-008 (filter + replay helpers:
:meth:`TwistsRepo.list_pending_for_chapter`,
:meth:`TwistsRepo.list_all_for_chapter_for_replay`,
:meth:`TwistsRepo.update_status_bulk`).

All methods operate on the caller-supplied ``AsyncSession``; the caller
is responsible for committing or rolling back the transaction.

Column notes:
  - ``public_id`` is a DB-generated UUID (gen_random_uuid()); callers
    never supply it.
  - ``status`` is constrained by ``ck_twists_status``:
    pending_review | approved | rejected_offensive |
    rejected_incoherent | rejected_spam | deleted_by_user.
  - ``deleted_at`` is set iff ``status='deleted_by_user'``
    (CHECK ``ck_twists_deleted_consistency``).

``lock_user_chapter`` acquires ``pg_advisory_xact_lock`` keyed on
``twist_quota:<user_id>:<chapter_id>`` with a 1-second lock_timeout
(FR-005). It raises :exc:`TwistLockBusy` if the lock cannot be acquired,
which the submission service (T-005) maps to HTTP 503 ``lock_busy``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession


class TwistLockBusy(Exception):
    """Raised when the per-(user, chapter) advisory lock cannot be
    acquired within 1 s.

    The submission service should translate this to HTTP 503 with
    ``{"code": "lock_busy"}``.
    """

    def __init__(self, user_id: int, chapter_id: int) -> None:
        self.user_id = user_id
        self.chapter_id = chapter_id
        super().__init__(
            f"Advisory lock for twist_quota:{user_id}:{chapter_id} "
            f"could not be acquired within 1 s"
        )


@dataclass
class Twist:
    """Flat projection of a ``twists`` row."""

    id: int
    public_id: UUID
    chapter_id: int
    user_id: int
    content: str
    status: str
    director_reason: str | None
    submitted_at: datetime
    reviewed_at: datetime | None
    deleted_at: datetime | None
    character_id: int


@dataclass(frozen=True)
class CharacterSnap:
    """Denormalized character fields joined from the ``characters`` table.

    Returned by :meth:`TwistsRepo.list_for_user_chapter_with_character`
    so the ``GET /me/twists`` endpoint can include the ``character`` block
    without a second round-trip.
    """

    slug: str
    display_name: str
    photo_r2_key: str


@dataclass(frozen=True)
class TwistWithChar:
    """A twist row paired with its character snapshot (LEFT JOIN result).

    ``character`` is ``None`` only if the FK target is unexpectedly missing;
    with the ``NOT NULL`` constraint on ``twists.character_id`` this should
    never happen in practice.
    """

    twist: Twist
    character: CharacterSnap | None


@dataclass(frozen=True)
class VerdictUpdate:
    """One verdict to persist via :meth:`TwistsRepo.update_status_bulk`.

    ``decision`` is one of ``approved``, ``rejected_offensive``,
    ``rejected_incoherent``, ``rejected_spam`` (matches
    :class:`app.domain.director_verdicts.Decision`). ``reason`` is the
    short Spanish text shown in ``/me/twists`` — caller MUST truncate to
    ≤80 chars before constructing this; the DB column is unconstrained
    but the contract (module 006) caps it.
    """

    twist_id: int
    decision: str
    reason: str


def _map_row(row: Any) -> Twist:
    return Twist(
        id=int(row["id"]),
        public_id=UUID(str(row["public_id"])),
        chapter_id=int(row["chapter_id"]),
        user_id=int(row["user_id"]),
        content=str(row["content"]),
        status=str(row["status"]),
        director_reason=(
            str(row["director_reason"])
            if row["director_reason"] is not None
            else None
        ),
        submitted_at=row["submitted_at"],
        reviewed_at=row["reviewed_at"],
        deleted_at=row["deleted_at"],
        character_id=int(row["character_id"]),
    )


_SELECT_COLS = (
    "id, public_id, chapter_id, user_id, content, status, "
    "director_reason, submitted_at, reviewed_at, deleted_at, character_id"
)


class TwistsRepo:
    """Repository for the ``twists`` table.

    Parameters
    ----------
    session:
        Active ``AsyncSession``. Caller manages commit/rollback.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def count_for_user_chapter(
        self, user_id: int, chapter_id: int
    ) -> int:
        """Return total twist count for (user, chapter), ALL statuses.

        Counts include ``deleted_by_user`` rows by design (FR-004
        anti-spam-then-delete): deletes do NOT free quota.
        """
        result = await self._s.execute(
            sa.text(
                "SELECT COUNT(*) FROM twists "
                "WHERE user_id = :user_id AND chapter_id = :chapter_id"
            ),
            {"user_id": user_id, "chapter_id": chapter_id},
        )
        return int(result.scalar_one())

    async def insert(
        self,
        chapter_id: int,
        user_id: int,
        content: str,
        character_id: int | None = None,
    ) -> Twist:
        """Insert a new twist with ``status='pending_review'`` and
        return the full row.

        ``public_id`` and ``submitted_at`` are populated by DB defaults.
        ``content`` is stored verbatim — callers MUST normalize first
        (see :func:`app.domain.twist_content.normalize`).

        ``character_id`` (Ronda 7, module 013) MUST point at an active row
        in ``characters``. When ``None`` is passed the column is filled
        with the lowest-``sort_order`` active character via a SELECT
        subquery — a backward-compat path so legacy tests that pre-date
        the FK keep working. **Production code paths always pass an
        explicit id** (the service layer validates it first via
        :class:`CharactersRepo.get_by_id_if_active`).
        """
        result = await self._s.execute(
            sa.text(
                "INSERT INTO twists (chapter_id, user_id, content, character_id) "
                "VALUES ("
                "  :chapter_id, :user_id, :content, "
                "  COALESCE("
                "    :character_id, "
                "    (SELECT id FROM characters "
                "     WHERE active = TRUE "
                "     ORDER BY sort_order ASC, id ASC LIMIT 1)"
                "  )"
                ") "
                f"RETURNING {_SELECT_COLS}"
            ),
            {
                "chapter_id": chapter_id,
                "user_id": user_id,
                "content": content,
                "character_id": character_id,
            },
        )
        row = result.mappings().one()
        return _map_row(row)

    async def get_by_public_id_for_update(
        self, public_id: UUID
    ) -> Twist | None:
        """Return the twist with this ``public_id``, locked ``FOR UPDATE``,
        or *None* if no such row.

        Used by the DELETE flow (T-005/T-006) to serialize concurrent
        deletes on the same row. The row lock is released when the
        caller's transaction commits or rolls back.
        """
        result = await self._s.execute(
            sa.text(
                f"SELECT {_SELECT_COLS} FROM twists "
                "WHERE public_id = :public_id "
                "FOR UPDATE"
            ),
            {"public_id": str(public_id)},
        )
        row = result.mappings().one_or_none()
        return _map_row(row) if row is not None else None

    async def soft_delete(self, twist_id: int) -> datetime:
        """Set ``status='deleted_by_user'`` + ``deleted_at=now()``;
        return the new ``deleted_at``.

        Does NOT free quota (FR-004 / FR-009). Caller is responsible
        for checking ownership and current status before calling.
        """
        result = await self._s.execute(
            sa.text(
                "UPDATE twists "
                "SET status = 'deleted_by_user', deleted_at = now() "
                "WHERE id = :id "
                "RETURNING deleted_at"
            ),
            {"id": twist_id},
        )
        deleted_at = result.scalar_one()
        assert isinstance(deleted_at, datetime)
        return deleted_at

    async def list_for_user_chapter(
        self,
        user_id: int,
        chapter_id: int,
        limit: int,
    ) -> list[Twist]:
        """Return up to ``limit`` twists for (user, chapter) ordered by
        ``submitted_at`` ASC.

        Includes ``deleted_by_user`` rows — the ``/me/twists`` UI needs
        to show users their own deletions for context.
        """
        result = await self._s.execute(
            sa.text(
                f"SELECT {_SELECT_COLS} FROM twists "
                "WHERE user_id = :user_id AND chapter_id = :chapter_id "
                "ORDER BY submitted_at ASC "
                "LIMIT :limit"
            ),
            {"user_id": user_id, "chapter_id": chapter_id, "limit": limit},
        )
        return [_map_row(row) for row in result.mappings()]

    async def list_for_user_chapter_with_character(
        self,
        user_id: int,
        chapter_id: int,
        limit: int,
    ) -> list[TwistWithChar]:
        """Like :meth:`list_for_user_chapter` but LEFT JOINs ``characters``.

        Returns each twist paired with its character snapshot so the
        ``GET /me/twists`` response can include the ``character`` block
        in a single query.
        """
        result = await self._s.execute(
            sa.text(
                "SELECT "
                "t.id, t.public_id, t.chapter_id, t.user_id, t.content, t.status, "
                "t.director_reason, t.submitted_at, t.reviewed_at, t.deleted_at, "
                "t.character_id, "
                "c.slug AS char_slug, "
                "c.display_name AS char_display_name, "
                "c.photo_r2_key AS char_photo_r2_key "
                "FROM twists t "
                "LEFT JOIN characters c ON c.id = t.character_id "
                "WHERE t.user_id = :user_id AND t.chapter_id = :chapter_id "
                "ORDER BY t.submitted_at ASC "
                "LIMIT :limit"
            ),
            {"user_id": user_id, "chapter_id": chapter_id, "limit": limit},
        )
        out: list[TwistWithChar] = []
        for row in result.mappings():
            twist = _map_row(row)
            char_snap: CharacterSnap | None = None
            if row["char_slug"] is not None:
                char_snap = CharacterSnap(
                    slug=str(row["char_slug"]),
                    display_name=str(row["char_display_name"]),
                    photo_r2_key=str(row["char_photo_r2_key"]),
                )
            out.append(TwistWithChar(twist=twist, character=char_snap))
        return out

    async def lock_user_chapter(
        self, user_id: int, chapter_id: int
    ) -> None:
        """Acquire ``pg_advisory_xact_lock`` for (user, chapter) with a
        1-second timeout.

        The lock is transaction-scoped — released automatically on the
        caller's commit/rollback. Serializes concurrent submits for the
        same (user, chapter) so the quota count check is race-safe
        (FR-005 / research R-001).

        Raises
        ------
        TwistLockBusy
            If the lock cannot be acquired within 1 second (55P03).
        """
        lock_key = f"twist_quota:{user_id}:{chapter_id}"
        try:
            await self._s.execute(
                sa.text("SET LOCAL lock_timeout = '1000ms'")
            )
            await self._s.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": lock_key},
            )
        except OperationalError as exc:
            orig = getattr(exc, "orig", None)
            if orig is not None and type(orig).__name__ == "LockNotAvailableError":
                raise TwistLockBusy(user_id, chapter_id) from exc
            raise

    # ------------------------------------------------------------------
    # Director's filter (module 006 / T-008)
    # ------------------------------------------------------------------

    async def list_pending_for_chapter(
        self, chapter_id: int
    ) -> list[Twist]:
        """Return all ``pending_review`` twists for ``chapter_id`` ordered
        by ``submitted_at ASC``.

        The deterministic ordering matters for batch chunking in the
        filter service (T-009): the same input always yields the same
        batches, so a partial run can be replayed without scrambling
        which twist landed in which batch (research R-009).

        Uses index ``idx_twists_chapter_status`` (module 005 migration
        0007).
        """
        result = await self._s.execute(
            sa.text(
                f"SELECT {_SELECT_COLS} FROM twists "
                "WHERE chapter_id = :chapter_id "
                "AND status = 'pending_review' "
                "ORDER BY submitted_at ASC"
            ),
            {"chapter_id": chapter_id},
        )
        return [_map_row(row) for row in result.mappings()]

    async def list_all_for_chapter_for_replay(
        self, chapter_id: int
    ) -> list[Twist]:
        """Return every twist for ``chapter_id`` except ``deleted_by_user``,
        ordered by ``submitted_at ASC``.

        Used by the admin replay endpoint (T-011) to re-classify already-
        classified twists. Borrados quedan afuera por contrato del
        data-model: jamás se re-clasifica algo que el usuario eliminó.
        """
        result = await self._s.execute(
            sa.text(
                f"SELECT {_SELECT_COLS} FROM twists "
                "WHERE chapter_id = :chapter_id "
                "AND status != 'deleted_by_user' "
                "ORDER BY submitted_at ASC"
            ),
            {"chapter_id": chapter_id},
        )
        return [_map_row(row) for row in result.mappings()]

    async def update_status_bulk(
        self,
        updates: list[VerdictUpdate],
        *,
        allow_already_classified: bool = False,
    ) -> int:
        """Apply N verdicts inside the caller's transaction; return the
        total number of rows actually updated.

        ``allow_already_classified=False`` (default, used by the FILTERING
        side-effect): each UPDATE is guarded by
        ``status = 'pending_review'``, so re-runs of the same batch are
        idempotent — a twist already classified is silently skipped (its
        rowcount contributes 0 to the return).

        ``allow_already_classified=True`` (used by the admin replay
        endpoint): the guard relaxes to ``status != 'deleted_by_user'``,
        so already-classified twists ARE re-written, but borrados nunca
        se tocan.

        Empty ``updates`` is a no-op that returns 0 without hitting the DB.
        """
        if not updates:
            return 0

        if allow_already_classified:
            stmt = sa.text(
                "UPDATE twists "
                "SET status = :decision, "
                "    director_reason = :reason, "
                "    reviewed_at = now() "
                "WHERE id = :twist_id "
                "AND status != 'deleted_by_user'"
            )
        else:
            stmt = sa.text(
                "UPDATE twists "
                "SET status = :decision, "
                "    director_reason = :reason, "
                "    reviewed_at = now() "
                "WHERE id = :twist_id "
                "AND status = 'pending_review'"
            )

        total = 0
        for u in updates:
            result = await self._s.execute(
                stmt,
                {
                    "decision": u.decision,
                    "reason": u.reason,
                    "twist_id": u.twist_id,
                },
            )
            total += cast(CursorResult[Any], result).rowcount or 0
        return total
