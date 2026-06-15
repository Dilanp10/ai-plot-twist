"""``POST /api/v1/internal/push/test`` — admin-triggered test push.

Module 011 / Task T-009.

Sends a test notification (FR-010 ``compose_test_notification``) to all
active subscriptions, or to a single user's subscriptions when
``user_public_id`` is supplied in the body.  No idempotency key, no
stale cleanup — this is an ops-verification tool, not the production
fan-out path.

Auth: ``Authorization: Bearer <ADMIN_TOKEN>``

Body (all optional)::

    {"user_public_id": "<UUID>"}   # omit to target every active sub

Response 200::

    {"sent": N, "failed": N, "gone": N, "subscription_count": N}

Error envelopes (RFC 7807):
  401 missing_admin_token  — Authorization header absent.
  403 bad_admin_token      — Token present but wrong.
  404 user_not_found       — user_public_id has no matching user.
  503 push_not_configured  — VAPID keys missing from settings.
"""

from __future__ import annotations

import json
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.domain.push_payload import compose_test_notification
from app.errors import ProblemDetail
from app.infra.push_subscriptions_repo import PushSubscriptionsRepo
from app.infra.webpush_sender import SendResult, WebPushSender
from app.logging import get_logger
from app.middleware.admin_token import verify_admin_token
from app.settings import Settings, get_settings

_log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/internal", tags=["internal"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PushTestRequest(BaseModel):
    user_public_id: UUID | None = None


class PushTestResponse(BaseModel):
    sent: int
    failed: int
    gone: int
    subscription_count: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_sender(settings: Settings) -> WebPushSender:
    """Construct a WebPushSender; raise 503 if VAPID keys are absent."""
    if not settings.vapid_private_key or not settings.vapid_public_key:
        raise ProblemDetail(
            status=503,
            code="push_not_configured",
            title="Push not configured",
            detail="VAPID_PRIVATE_KEY or VAPID_PUBLIC_KEY is not set on the server.",
        )
    return WebPushSender(
        vapid_private_key=settings.vapid_private_key,
        vapid_subject=settings.vapid_subject,
    )


async def _resolve_user_id(session: AsyncSession, public_id: UUID) -> int:
    """Return internal user id for public_id; raise 404 if not found."""
    row = (
        await session.execute(
            sa.text("SELECT id FROM users WHERE public_id = :pid"),
            {"pid": str(public_id)},
        )
    ).one_or_none()
    if row is None:
        raise ProblemDetail(
            status=404,
            code="user_not_found",
            title="User not found",
            detail=f"No user with public_id={public_id}.",
        )
    return int(row[0])


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post(
    "/push/test",
    operation_id="postInternalPushTest",
    summary="Send a test push notification to verify VAPID config (admin-only)",
    response_model=PushTestResponse,
    dependencies=[Depends(verify_admin_token)],
)
async def post_internal_push_test(
    body: PushTestRequest,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> PushTestResponse:
    """Send a test push to every active subscription (or one user's subs).

    Builds a :class:`WebPushSender` from the server's VAPID keys.
    Sends sequentially — the audience here is small (ops verification).
    Gone subscriptions are bulk-deleted; no stale-cleanup pass.
    """
    sender = _build_sender(settings)
    repo = PushSubscriptionsRepo(session)

    if body.user_public_id is not None:
        user_id = await _resolve_user_id(session, body.user_public_id)
        subs = await repo.list_active_for_user(user_id)
    else:
        subs = await repo.list_active_all()

    payload_bytes = json.dumps(compose_test_notification()).encode("utf-8")

    sent = 0
    failed = 0
    gone_ids: list[int] = []

    for sub in subs:
        outcome = await sender.send(sub, payload_bytes)
        if outcome.result == SendResult.SUCCESS:
            await repo.mark_success(sub.id)
            sent += 1
        elif outcome.result == SendResult.GONE:
            gone_ids.append(sub.id)
        else:
            await repo.mark_failure(sub.id)
            failed += 1

    if gone_ids:
        await repo.bulk_delete(gone_ids)

    await session.commit()

    _log.info(
        "push_test_completed",
        user_public_id=str(body.user_public_id) if body.user_public_id else None,
        subscription_count=len(subs),
        sent=sent,
        failed=failed,
        gone=len(gone_ids),
    )

    return PushTestResponse(
        sent=sent,
        failed=failed,
        gone=len(gone_ids),
        subscription_count=len(subs),
    )
