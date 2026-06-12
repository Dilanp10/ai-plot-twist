"""IdempotencyRepo — SQLAlchemy Core repository for ``idempotency_keys``.

Module 005 / Task T-005 (first writer of this table).

The table itself was shipped in migration 0001 (baseline) as a forward
declaration; this is the first repo wrapping it. All methods operate on
the caller-supplied ``AsyncSession``; the caller manages commit/rollback.

Model:
  - ``key`` is opaque (typically a client UUIDv4).
  - ``request_hash`` is a SHA-256 of the canonical request body, computed
    by the HTTP layer (this repo does not opine on the hash format).
  - ``response_json`` is the cached response payload — for /twists/submit
    this is the full body that will be re-served verbatim on replay.

Cleanup of expired rows (> 14 d per spec FR-010) is left to a future cron
job using the existing ``idx_idem_created`` index.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class IdempotencyRecord:
    """Cached request/response pair keyed by Idempotency-Key."""

    key: str
    user_id: int | None
    request_hash: str
    response_json: dict[str, Any]
    created_at: datetime


def _map_row(row: Any) -> IdempotencyRecord:
    raw_response = row["response_json"]
    return IdempotencyRecord(
        key=str(row["key"]),
        user_id=int(row["user_id"]) if row["user_id"] is not None else None,
        request_hash=str(row["request_hash"]),
        response_json=dict(raw_response) if raw_response is not None else {},
        created_at=row["created_at"],
    )


_SELECT_COLS = "key, user_id, request_hash, response_json, created_at"


class IdempotencyRepo:
    """Repository for the ``idempotency_keys`` table.

    Parameters
    ----------
    session:
        Active ``AsyncSession``. Caller manages commit/rollback.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, key: str) -> IdempotencyRecord | None:
        """Return the cached record for *key*, or *None* if not found."""
        result = await self._s.execute(
            sa.text(
                f"SELECT {_SELECT_COLS} FROM idempotency_keys "
                "WHERE key = :key"
            ),
            {"key": key},
        )
        row = result.mappings().one_or_none()
        return _map_row(row) if row is not None else None

    async def insert(
        self,
        key: str,
        user_id: int | None,
        request_hash: str,
        response_json: dict[str, Any],
    ) -> None:
        """Persist a new idempotency record.

        Raises
        ------
        sqlalchemy.exc.IntegrityError
            If the key already exists. Callers should ``get()`` first;
            concurrent inserts must serialize externally (e.g. the
            advisory lock used by :class:`TwistSubmissionService`).
        """
        await self._s.execute(
            sa.text(
                "INSERT INTO idempotency_keys "
                "(key, user_id, request_hash, response_json) "
                "VALUES (:key, :user_id, :request_hash, "
                "        cast(:response_json AS jsonb))"
            ),
            {
                "key": key,
                "user_id": user_id,
                "request_hash": request_hash,
                "response_json": json.dumps(response_json),
            },
        )
