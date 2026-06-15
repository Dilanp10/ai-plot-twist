"""Push subscription endpoints (FR-011, FR-012).

Module 011 / Tasks T-007 + T-008.

Endpoints
---------
GET    /api/v1/push/public-key              — VAPID public key (unauthenticated)
POST   /api/v1/push/subscribe               — Register / refresh a subscription
DELETE /api/v1/push/subscriptions/{sub_id}  — Unsubscribe
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import ProblemDetail
from app.infra.push_subscriptions_repo import PushSubscriptionsRepo
from app.infra.users_repo import UserRow
from app.middleware.jwt_auth import require_user
from app.settings import Settings, get_settings

router = APIRouter(prefix="/api/v1/push", tags=["push"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PublicKeyResponse(BaseModel):
    public_key: str


class SubscribeRequest(BaseModel):
    endpoint: str
    p256dh: str
    auth: str
    user_agent: str | None = None


class SubscribeResponse(BaseModel):
    id: int


# ---------------------------------------------------------------------------
# GET /api/v1/push/public-key  (T-007)
# ---------------------------------------------------------------------------


@router.get(
    "/public-key",
    operation_id="getPushPublicKey",
    summary="Return the VAPID public key for push subscription setup",
    response_model=PublicKeyResponse,
)
async def get_push_public_key(
    settings: Settings = Depends(get_settings),
) -> PublicKeyResponse:
    """Return the server's VAPID public key.

    Unauthenticated — the PWA fetches this before requesting notification
    permission.  503 when VAPID keys are not configured on the server.
    """
    if not settings.vapid_public_key:
        raise ProblemDetail(
            status=503,
            code="push_not_configured",
            title="Push not configured",
            detail="VAPID_PUBLIC_KEY is not set on the server.",
        )
    return PublicKeyResponse(public_key=settings.vapid_public_key)


# ---------------------------------------------------------------------------
# POST /api/v1/push/subscribe  (T-008)
# ---------------------------------------------------------------------------


@router.post(
    "/subscribe",
    operation_id="postPushSubscribe",
    summary="Register or refresh a Web Push subscription",
    response_model=SubscribeResponse,
    status_code=201,
)
async def post_push_subscribe(
    body: SubscribeRequest,
    user: UserRow = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> SubscribeResponse:
    """Upsert a push subscription for the authenticated user.

    Re-subscribing from the same browser reuses the existing row and
    resets failure_count — the endpoint is idempotent on endpoint reuse.
    """
    repo = PushSubscriptionsRepo(session)
    sub_id = await repo.upsert(
        user_id=user.id,
        endpoint=body.endpoint,
        p256dh=body.p256dh,
        auth=body.auth,
        ua=body.user_agent,
    )
    await session.commit()
    return SubscribeResponse(id=sub_id)


# ---------------------------------------------------------------------------
# DELETE /api/v1/push/subscriptions/{sub_id}  (T-008)
# ---------------------------------------------------------------------------


@router.delete(
    "/subscriptions/{sub_id}",
    operation_id="deletePushSubscription",
    summary="Remove a Web Push subscription",
    status_code=204,
    response_class=Response,
)
async def delete_push_subscription(
    sub_id: int,
    user: UserRow = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Hard-delete a subscription owned by the authenticated user.

    Returns 404 when ``sub_id`` does not exist or belongs to a different user.
    """
    repo = PushSubscriptionsRepo(session)
    deleted = await repo.delete_by_id_for_user(subscription_id=sub_id, user_id=user.id)
    if not deleted:
        raise ProblemDetail(
            status=404,
            code="subscription_not_found",
            title="Subscription not found",
            detail=f"No subscription with id={sub_id} belongs to this user.",
        )
    await session.commit()
    return Response(status_code=204)
