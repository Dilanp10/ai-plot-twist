"""InvitesRepo — SQLAlchemy Core repository for the ``invites`` table.

All methods operate on the caller-supplied ``AsyncSession``; the caller
is responsible for committing or rolling back the transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.invites import InviteCode


@dataclass
class InviteRow:
    """Flat projection of the ``invites`` table row."""

    code: str
    issued_by: str
    issued_at: datetime
    expires_at: datetime
    status: str
    redeemed_at: datetime | None
    redeemed_by_user: int | None
    note: str | None


def _map_row(row: Any) -> InviteRow:
    return InviteRow(
        code=row["code"],
        issued_by=row["issued_by"],
        issued_at=row["issued_at"],
        expires_at=row["expires_at"],
        status=row["status"],
        redeemed_at=row["redeemed_at"],
        redeemed_by_user=row["redeemed_by_user"],
        note=row["note"],
    )


_SELECT_COLS = (
    "code, issued_by, issued_at, expires_at, status, "
    "redeemed_at, redeemed_by_user, note"
)


class InvitesRepo:
    """Repository for the ``invites`` table.

    Parameters
    ----------
    session:
        Active ``AsyncSession``.  Caller manages commit/rollback.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def insert(
        self,
        code: InviteCode,
        expires_at: datetime,
        issued_by: str,
        note: str | None = None,
    ) -> InviteRow:
        """Insert a new ``unused`` invite.  Returns the persisted row."""
        result = await self._s.execute(
            sa.text(
                f"INSERT INTO invites (code, issued_by, expires_at, status, note) "
                f"VALUES (:code, :issued_by, :expires_at, 'unused', :note) "
                f"RETURNING {_SELECT_COLS}"
            ),
            {
                "code": str(code),
                "issued_by": issued_by,
                "expires_at": expires_at,
                "note": note,
            },
        )
        return _map_row(result.mappings().one())

    async def get_for_update(self, code: InviteCode) -> InviteRow | None:
        """Select the invite row with a ``FOR UPDATE`` lock.

        Returns ``None`` if the code does not exist.
        """
        result = await self._s.execute(
            sa.text(
                f"SELECT {_SELECT_COLS} FROM invites "
                f"WHERE code = :code FOR UPDATE"
            ),
            {"code": str(code)},
        )
        row = result.mappings().one_or_none()
        return _map_row(row) if row is not None else None

    async def mark_redeemed(self, code: InviteCode, user_id: int) -> None:
        """Transition ``status`` → ``'redeemed'`` and record the redeemer."""
        await self._s.execute(
            sa.text(
                "UPDATE invites "
                "SET status = 'redeemed', "
                "    redeemed_at = now(), "
                "    redeemed_by_user = :uid "
                "WHERE code = :code"
            ),
            {"uid": user_id, "code": str(code)},
        )

    async def revoke(self, code: InviteCode) -> None:
        """Transition ``status`` → ``'revoked'``."""
        await self._s.execute(
            sa.text("UPDATE invites SET status = 'revoked' WHERE code = :code"),
            {"code": str(code)},
        )

    async def list_all(self) -> list[InviteRow]:
        """Return all invites ordered by ``issued_at DESC``."""
        result = await self._s.execute(
            sa.text(
                f"SELECT {_SELECT_COLS} FROM invites ORDER BY issued_at DESC"
            )
        )
        return [_map_row(row) for row in result.mappings()]
