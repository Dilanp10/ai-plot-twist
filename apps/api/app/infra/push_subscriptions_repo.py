"""PushSubscriptionsRepo — CRUD + fan-out queries + stale cleanup.

Module 011 / Task T-003.

UPSERT is the bedrock: re-subscribing from the same browser (after
clearing site data or user-switching) re-binds the existing
``endpoint`` row to the current user and resets ``failure_count`` to 0.

Methods bias towards the fan-out call path: ``list_active_all`` joins
users to filter out banned authors so the orchestrator can hand a
single result set to its bounded concurrency pool. Per-user methods
(``list_active_for_user``, ``delete_by_id_for_user``) carry the
``user_id`` through to enforce row-level ownership without a separate
authz check at the call site.

Stale cleanup uses the threshold-based rule from research R-005:
  ``failure_count >= threshold AND
   (last_success_at IS NULL OR last_success_at < now() - INTERVAL '7 days')``

The 7-day floor protects established subscriptions from being culled
on a single bad-day spike — a brand-new sub with 3 failures and no
successes is dead, but an old sub with one transient failure is just
flaky.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class Subscription:
    """Settled row from ``push_subscriptions`` for in-memory fan-out.

    Mirrors the columns the orchestrator (T-006) needs to call
    :class:`~app.infra.webpush_sender.WebPushSender.send`. The user's
    UUID is omitted intentionally — push payloads are user-agnostic
    per FR-010 (no PII).
    """

    id: int
    user_id: int
    endpoint: str
    p256dh_key: str
    auth_key: str


class PushSubscriptionsRepo:
    """Repository for the ``push_subscriptions`` table.

    Caller manages commit/rollback (caller-owned transactions, same as
    the rest of the codebase's repos).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #

    async def upsert(
        self,
        *,
        user_id: int,
        endpoint: str,
        p256dh: str,
        auth: str,
        ua: str | None,
    ) -> int:
        """INSERT a new subscription or rebind an existing endpoint.

        ``ON CONFLICT (endpoint) DO UPDATE`` re-binds the row to the
        current user, resets failure_count to 0, and refreshes the
        cryptographic material. Returns the row id either way.
        """
        result = await self._s.execute(
            sa.text(
                "INSERT INTO push_subscriptions "
                "  (user_id, endpoint, p256dh_key, auth_key, user_agent) "
                "VALUES "
                "  (:user_id, :endpoint, :p256dh, :auth, :ua) "
                "ON CONFLICT (endpoint) DO UPDATE "
                "  SET user_id       = EXCLUDED.user_id, "
                "      p256dh_key    = EXCLUDED.p256dh_key, "
                "      auth_key      = EXCLUDED.auth_key, "
                "      user_agent    = EXCLUDED.user_agent, "
                "      failure_count = 0 "
                "RETURNING id"
            ),
            {
                "user_id": user_id,
                "endpoint": endpoint,
                "p256dh": p256dh,
                "auth": auth,
                "ua": ua,
            },
        )
        return int(result.scalar_one())

    async def delete_by_id_for_user(
        self, *, subscription_id: int, user_id: int
    ) -> bool:
        """Hard-delete a subscription IFF it belongs to ``user_id``.

        Returns ``True`` when a row was deleted, ``False`` when the
        composite (id, user_id) matched nothing. The route layer maps
        ``False`` to 404 so a token-spray can't enumerate IDs.
        """
        result = await self._s.execute(
            sa.text(
                "DELETE FROM push_subscriptions "
                "WHERE id = :sid AND user_id = :uid"
            ),
            {"sid": subscription_id, "uid": user_id},
        )
        return (cast(CursorResult[Any], result).rowcount or 0) > 0

    async def mark_success(self, subscription_id: int) -> None:
        """Bump ``last_success_at`` to now() and reset ``failure_count``.

        Called after a 201/204 from the push service. Resetting the
        counter is intentional: one good push is enough to redeem a
        flaky endpoint and protect it from the stale-cleanup sweep.
        """
        await self._s.execute(
            sa.text(
                "UPDATE push_subscriptions "
                "SET last_success_at = now(), "
                "    failure_count   = 0 "
                "WHERE id = :sid"
            ),
            {"sid": subscription_id},
        )

    async def mark_failure(self, subscription_id: int) -> None:
        """Increment ``failure_count`` (non-Gone failures only).

        ``Gone`` (HTTP 410) bypasses this — the orchestrator deletes
        such rows immediately via :meth:`bulk_delete`. Everything else
        (5xx, timeout, 4xx-not-410) increments the counter; when it
        crosses the threshold AND no recent success exists,
        :meth:`cleanup_stale` finishes the job.
        """
        await self._s.execute(
            sa.text(
                "UPDATE push_subscriptions "
                "SET failure_count = failure_count + 1 "
                "WHERE id = :sid"
            ),
            {"sid": subscription_id},
        )

    async def bulk_delete(self, subscription_ids: Sequence[int]) -> int:
        """Hard-delete every subscription in ``subscription_ids``.

        Returns the number of rows deleted. The orchestrator passes
        the set of Gone (410) endpoints collected during a fan-out so
        the next sweep doesn't waste a request on them. No-op (returns
        0) when the list is empty.
        """
        if not subscription_ids:
            return 0
        result = await self._s.execute(
            sa.text("DELETE FROM push_subscriptions WHERE id = ANY(:ids)"),
            {"ids": list(subscription_ids)},
        )
        return int(cast(CursorResult[Any], result).rowcount or 0)

    async def cleanup_stale(self, *, threshold: int) -> int:
        """Delete subscriptions whose failure_count has crossed the
        threshold AND have no recent success (R-005).

        Returns the row count. The threshold comes from settings
        (``PUSH_FAILURE_THRESHOLD``, default 3); the 7-day floor on
        ``last_success_at`` is hard-coded per R-005 and intentionally
        NOT configurable — a tuning knob that wasn't load-bearing.
        """
        result = await self._s.execute(
            sa.text(
                "DELETE FROM push_subscriptions "
                "WHERE failure_count >= :threshold "
                "  AND (last_success_at IS NULL "
                "       OR last_success_at < now() - INTERVAL '7 days')"
            ),
            {"threshold": threshold},
        )
        return int(cast(CursorResult[Any], result).rowcount or 0)

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    async def list_active_for_user(
        self, user_id: int
    ) -> list[Subscription]:
        """All subscriptions owned by ``user_id``, newest first.

        Used by the Settings screen so the user can see and revoke
        per-device. ``user_id`` is the integer id; the route layer
        translates the JWT's UUID upstream.
        """
        rows = (
            await self._s.execute(
                sa.text(
                    "SELECT id, user_id, endpoint, p256dh_key, auth_key "
                    "FROM push_subscriptions "
                    "WHERE user_id = :uid "
                    "ORDER BY created_at DESC"
                ),
                {"uid": user_id},
            )
        ).mappings().all()
        return [
            Subscription(
                id=int(r["id"]),
                user_id=int(r["user_id"]),
                endpoint=str(r["endpoint"]),
                p256dh_key=str(r["p256dh_key"]),
                auth_key=str(r["auth_key"]),
            )
            for r in rows
        ]

    async def list_active_all(self) -> list[Subscription]:
        """All subscriptions of non-banned users — the fan-out target set.

        JOINs ``users`` so banned-author subscriptions never receive
        a push. Banned users get unsubscribed implicitly when their
        rows get cascade-deleted, but the JOIN guards against the
        in-between state where ``is_banned = TRUE`` but the row
        hasn't been cleaned up.
        """
        rows = (
            await self._s.execute(
                sa.text(
                    "SELECT ps.id, ps.user_id, ps.endpoint, "
                    "       ps.p256dh_key, ps.auth_key "
                    "FROM push_subscriptions AS ps "
                    "JOIN users AS u ON u.id = ps.user_id "
                    "WHERE u.is_banned = FALSE"
                )
            )
        ).mappings().all()
        return [
            Subscription(
                id=int(r["id"]),
                user_id=int(r["user_id"]),
                endpoint=str(r["endpoint"]),
                p256dh_key=str(r["p256dh_key"]),
                auth_key=str(r["auth_key"]),
            )
            for r in rows
        ]
