"""RateLimitRepo — atomic hourly rate-limit buckets.

Uses an ``INSERT … ON CONFLICT DO UPDATE`` pattern so the increment is
atomic — no separate SELECT + UPDATE race condition.

The window key is ``date_trunc('hour', now())`` computed server-side, so
each bucket covers exactly one calendar hour in the DB server's timezone
(UTC).

Raises :exc:`RateLimited` when the new count exceeds the configured
``max_per_window``.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


class RateLimited(Exception):
    """Raised when a rate-limit bucket exceeds its maximum count.

    Attributes
    ----------
    bucket_key:
        The key that was rate-limited (e.g. ``"redeem:ip:1.2.3.4"``).
    count:
        The count that triggered the limit.
    max_count:
        The configured maximum.
    """

    def __init__(self, bucket_key: str, count: int, max_count: int) -> None:
        super().__init__(
            f"Rate limit excedido para '{bucket_key}': {count}/{max_count}"
        )
        self.bucket_key = bucket_key
        self.count = count
        self.max_count = max_count


class RateLimitRepo:
    """Repository for the ``rate_limit_buckets`` table.

    Parameters
    ----------
    session:
        Active ``AsyncSession``.  Caller manages commit/rollback.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def check_and_increment(
        self,
        bucket_key: str,
        max_per_window: int,
    ) -> int:
        """Atomically increment the counter for *bucket_key* in the current hour.

        Parameters
        ----------
        bucket_key:
            Arbitrary string key, e.g. ``"redeem:ip:1.2.3.4"``.
        max_per_window:
            Maximum allowed count in the current hour window.

        Returns
        -------
        int
            The new count after incrementing.

        Raises
        ------
        RateLimited
            If the new count exceeds *max_per_window*.
        """
        result = await self._s.execute(
            sa.text(
                "INSERT INTO rate_limit_buckets (bucket_key, window_start, count) "
                "VALUES (:key, date_trunc('hour', now()), 1) "
                "ON CONFLICT (bucket_key, window_start) "
                "DO UPDATE SET count = rate_limit_buckets.count + 1 "
                "RETURNING count"
            ),
            {"key": bucket_key},
        )
        count: int = int(result.scalar_one())
        if count > max_per_window:
            raise RateLimited(bucket_key, count, max_per_window)
        return count
