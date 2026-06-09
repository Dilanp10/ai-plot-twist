"""Auth router — invite redemption, JWT refresh, user introspection.

Endpoints
---------
POST /api/v1/auth/redeem-invite  (T-015)
POST /api/v1/auth/refresh        (T-016)
GET  /api/v1/auth/me             (T-017)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Depends, Request, Response
from fastapi.routing import APIRouter
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.domain import display_name as dn
from app.domain.device_secret import mint
from app.domain.invites import InviteCode
from app.domain.jwt_service import JWTService
from app.errors import ProblemDetail
from app.infra.invites_repo import InvitesRepo
from app.infra.rate_limit_repo import RateLimited, RateLimitRepo
from app.infra.users_repo import UsersRepo
from app.settings import get_settings

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_RATE_LIMIT_MAX = 5  # requests per hour per IP

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class PublicUser(BaseModel):
    public_id: UUID
    display_name: str
    created_at: datetime
    last_seen_at: datetime


class RedeemRequest(BaseModel):
    invite_code: str
    display_name: str

    @field_validator("invite_code")
    @classmethod
    def parse_invite_code(cls, v: str) -> str:
        try:
            return str(InviteCode.parse(v))
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class RedeemResponse(BaseModel):
    user: PublicUser
    jwt: str
    device_secret: str
    jwt_expires_at: datetime


class RefreshRequest(BaseModel):
    device_secret: str


class RefreshResponse(BaseModel):
    jwt: str
    jwt_expires_at: datetime


class MeResponse(BaseModel):
    user: PublicUser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_ip(request: Request) -> str:
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _retry_after_seconds() -> int:
    now = datetime.now(UTC)
    next_hour = (now + timedelta(hours=1)).replace(
        minute=0, second=0, microsecond=0
    )
    return max(1, int((next_hour - now).total_seconds()))


# ---------------------------------------------------------------------------
# POST /api/v1/auth/redeem-invite  (T-015)
# ---------------------------------------------------------------------------


@router.post(
    "/redeem-invite",
    status_code=201,
    response_model=RedeemResponse,
    operation_id="postAuthRedeemInvite",
)
async def redeem_invite(
    body: RedeemRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> RedeemResponse:
    """Redeem an invite code, create a user, and issue a JWT + device_secret."""
    settings = get_settings()
    ip = _get_ip(request)
    code = InviteCode.parse(body.invite_code)

    # 1. Rate-limit check
    rate_repo = RateLimitRepo(session)
    try:
        await rate_repo.check_and_increment(
            bucket_key=f"redeem:ip:{ip}",
            max_per_window=_RATE_LIMIT_MAX,
        )
    except RateLimited:
        response.headers["Retry-After"] = str(_retry_after_seconds())
        raise ProblemDetail(
            status=429,
            code="rate_limited",
            title="Demasiados intentos",
            detail="Probá en una hora.",
        ) from None

    # 2. Normalize display_name (fail fast before locking the invite row)
    try:
        normalized_name = dn.normalize(body.display_name)
    except ValueError as exc:
        raise ProblemDetail(
            status=409,
            code="display_name_invalid",
            title="Nombre de usuario inválido",
            detail=str(exc),
        ) from exc

    # 3. Lock invite row
    invite_repo = InvitesRepo(session)
    invite = await invite_repo.get_for_update(code)
    now = datetime.now(UTC)

    if (
        invite is None
        or invite.status != "unused"
        or invite.expires_at.replace(tzinfo=UTC) < now
    ):
        raise ProblemDetail(
            status=404,
            code="invite_not_redeemable",
            title="Invite not redeemable",
            detail="Ese código no es válido o ya fue usado.",
        )

    # 4. Mint device secret
    raw_secret, token_hash = mint()

    # 5. Insert user
    users_repo = UsersRepo(session)
    user = await users_repo.create(
        display_name=normalized_name,
        invite_code=code,
        device_token_hash=token_hash,
    )

    # 6. Mark invite redeemed
    await invite_repo.mark_redeemed(code, user.id)

    # 7. Commit
    await session.commit()

    # 8. Issue JWT (after commit so user row is stable)
    jwt_token, jwt_exp = JWTService(settings.jwt_secret).issue(user.public_id)

    return RedeemResponse(
        user=PublicUser(
            public_id=user.public_id,
            display_name=user.display_name,
            created_at=user.created_at,
            last_seen_at=user.last_seen_at,
        ),
        jwt=jwt_token,
        device_secret=raw_secret,
        jwt_expires_at=jwt_exp,
    )
