"""UsersRepo — SQLAlchemy Core repository for the ``users`` table.

All methods operate on the caller-supplied ``AsyncSession``; the caller
is responsible for committing or rolling back the transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.invites import InviteCode


@dataclass
class UserRow:
    """Flat projection of the ``users`` table row."""

    id: int
    public_id: UUID
    display_name: str
    invite_code: str
    device_token: str
    created_at: datetime
    last_seen_at: datetime
    is_banned: bool


def _map_row(row: Any) -> UserRow:
    return UserRow(
        id=int(row["id"]),
        public_id=UUID(str(row["public_id"])),
        display_name=str(row["display_name"]),
        invite_code=str(row["invite_code"]),
        device_token=str(row["device_token"]),
        created_at=row["created_at"],
        last_seen_at=row["last_seen_at"],
        is_banned=bool(row["is_banned"]),
    )


_SELECT_COLS = (
    "id, public_id, display_name, invite_code, device_token, "
    "created_at, last_seen_at, is_banned"
)


class UsersRepo:
    """Repository for the ``users`` table.

    Parameters
    ----------
    session:
        Active ``AsyncSession``.  Caller manages commit/rollback.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def create(
        self,
        display_name: str,
        invite_code: InviteCode,
        device_token_hash: str,
    ) -> UserRow:
        """Insert a new user row and return the persisted record.

        Parameters
        ----------
        display_name:
            Already-normalised display name (caller should call
            ``display_name.normalize()`` first).
        invite_code:
            Validated invite code (must reference an existing ``invites`` row).
        device_token_hash:
            SHA-256 hex digest of the device secret (64 hex chars).
        """
        result = await self._s.execute(
            sa.text(
                f"INSERT INTO users (display_name, invite_code, device_token) "
                f"VALUES (:display_name, :invite_code, :device_token) "
                f"RETURNING {_SELECT_COLS}"
            ),
            {
                "display_name": display_name,
                "invite_code": str(invite_code),
                "device_token": device_token_hash,
            },
        )
        return _map_row(result.mappings().one())

    async def get_by_public_id(self, public_id: UUID) -> UserRow | None:
        """Look up a user by their public UUID.  Returns ``None`` if not found."""
        result = await self._s.execute(
            sa.text(
                f"SELECT {_SELECT_COLS} FROM users WHERE public_id = :public_id"
            ),
            {"public_id": public_id},
        )
        row = result.mappings().one_or_none()
        return _map_row(row) if row is not None else None

    async def touch_last_seen(self, public_id: UUID) -> None:
        """Update ``last_seen_at`` to ``now()`` for the given user."""
        await self._s.execute(
            sa.text(
                "UPDATE users SET last_seen_at = now() WHERE public_id = :public_id"
            ),
            {"public_id": public_id},
        )

    async def get_by_device_token(self, hash_hex: str) -> UserRow | None:
        """Look up a user by stored device token hash.  Returns ``None`` if not found."""
        result = await self._s.execute(
            sa.text(
                f"SELECT {_SELECT_COLS} FROM users WHERE device_token = :hash"
            ),
            {"hash": hash_hex},
        )
        row = result.mappings().one_or_none()
        return _map_row(row) if row is not None else None
