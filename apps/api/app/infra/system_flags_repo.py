"""SystemFlagsRepo — SQLAlchemy Core repository for the ``system_flags`` table.

Module 003 / Task T-011.

All write methods operate on the caller-supplied ``AsyncSession``; the caller
is responsible for committing or rolling back the transaction.

In-process cache:
  ``get()`` caches each flag value for ``_TTL_SECONDS`` (30 s) to avoid
  a DB round-trip on every executor call.  The cache is a simple module-level
  dict — safe in a single-threaded asyncio event loop because dict reads and
  writes are atomic and coroutines only interleave at ``await`` points.

  ``set()`` invalidates the cache entry for the written key immediately, so
  the next ``get()`` always reflects the freshly persisted value.

  Call ``clear_cache()`` to flush all entries (e.g. between integration tests).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# In-process cache
# ---------------------------------------------------------------------------

_TTL_SECONDS: float = 30.0

# key → (FlagValue, monotonic_expiry)
_cache: dict[str, tuple[FlagValue, float]] = {}


def clear_cache() -> None:
    """Flush all cached flag values.

    Intended for use in tests and after bulk flag updates.
    """
    _cache.clear()


# ---------------------------------------------------------------------------
# Row projection
# ---------------------------------------------------------------------------


@dataclass
class FlagValue:
    """Flat projection of a ``system_flags`` row."""

    flag_key: str
    flag_value: dict[str, Any]
    updated_by: str
    updated_at: datetime


def _map_row(row: Any) -> FlagValue:
    raw = row["flag_value"]
    return FlagValue(
        flag_key=str(row["flag_key"]),
        flag_value=dict(raw) if raw is not None else {},
        updated_by=str(row["updated_by"]),
        updated_at=row["updated_at"],
    )


_SELECT_COLS = "flag_key, flag_value, updated_by, updated_at"


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class SystemFlagsRepo:
    """Repository for the ``system_flags`` table.

    Parameters
    ----------
    session:
        Active ``AsyncSession``.  Caller manages commit/rollback.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, key: str) -> FlagValue | None:
        """Return the flag for *key*, using the 30 s in-process cache.

        Returns *None* if the key does not exist.
        """
        entry = _cache.get(key)
        if entry is not None and time.monotonic() < entry[1]:
            return entry[0]

        result = await self._s.execute(
            sa.text(
                f"SELECT {_SELECT_COLS} FROM system_flags "
                "WHERE flag_key = :key"
            ),
            {"key": key},
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None

        value = _map_row(row)
        _cache[key] = (value, time.monotonic() + _TTL_SECONDS)
        return value

    async def set(
        self,
        key: str,
        value: dict[str, Any],
        updated_by: str,
    ) -> FlagValue:
        """Upsert a flag value and return the persisted row.

        Creates the row if it does not exist; updates it if it does.
        Invalidates the in-process cache entry for *key* immediately.

        Parameters
        ----------
        key:
            Flag key (e.g. ``"kill_switch"``).
        value:
            JSON-serialisable dict representing the new flag value.
        updated_by:
            Identity of the actor making the change (e.g. ``"admin-cli"``).

        Returns
        -------
        FlagValue
            The row as it was persisted (includes the DB-generated
            ``updated_at``).
        """
        result = await self._s.execute(
            sa.text(
                "INSERT INTO system_flags (flag_key, flag_value, updated_by) "
                "VALUES (:key, :value::jsonb, :updated_by) "
                "ON CONFLICT (flag_key) DO UPDATE SET "
                "    flag_value = EXCLUDED.flag_value, "
                "    updated_by = EXCLUDED.updated_by, "
                "    updated_at = now() "
                f"RETURNING {_SELECT_COLS}"
            ),
            {
                "key": key,
                "value": json.dumps(value),
                "updated_by": updated_by,
            },
        )
        flag = _map_row(result.mappings().one())
        # Invalidate so the next get() fetches the fresh DB value.
        _cache.pop(key, None)
        return flag

    async def is_kill_switch_on(self) -> bool:
        """Return whether the kill-switch flag is currently active.

        Reads from cache (30 s TTL).  Returns *False* (fail-open) if the
        ``kill_switch`` row is missing — the migration seeds it as ``False``
        so this branch should never fire in production.
        """
        flag = await self.get("kill_switch")
        if flag is None:
            return False
        return bool(flag.flag_value.get("on", False))
