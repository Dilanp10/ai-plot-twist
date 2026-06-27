"""Admin authentication — password verification + admin-scoped JWT.

Admin JWTs are distinct from user JWTs:
  - sub  : "admin" (not a UUID)
  - scope: "admin"
  - exp  : now + 8 h (shorter than the 90-day user token)
  - aud  : "aiplottwist" (same audience)

Usage::

    # Issue
    token = issue_admin_jwt(settings.jwt_secret)

    # Verify (FastAPI dependency)
    @router.get("/admin/cycle")
    async def get_cycle(_: None = Depends(require_admin_jwt)) -> ...:
        ...
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, Header

from app.errors import ProblemDetail
from app.settings import Settings, get_settings

__all__ = [
    "verify_admin_password",
    "issue_admin_jwt",
    "verify_admin_jwt",
    "require_admin_jwt",
]

_ALGORITHM = "HS256"
_AUDIENCE = "aiplottwist"
_TTL_HOURS = 8
_SCOPE = "admin"


def verify_admin_password(password: str, settings: Settings) -> bool:
    """Return True iff *password* matches ADMIN_PASSWORD (timing-safe)."""
    if not settings.admin_password:
        return False
    return hmac.compare_digest(password.encode(), settings.admin_password.encode())


def issue_admin_jwt(jwt_secret: str) -> str:
    """Sign and return a new admin JWT valid for 8 hours."""
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        "sub": "admin",
        "scope": _SCOPE,
        "aud": _AUDIENCE,
        "iat": now,
        "exp": now + timedelta(hours=_TTL_HOURS),
    }
    return jwt.encode(payload, jwt_secret, algorithm=_ALGORITHM)


def verify_admin_jwt(token: str, jwt_secret: str) -> bool:
    """Return True iff *token* is a valid, unexpired admin JWT."""
    try:
        decoded: dict[str, object] = jwt.decode(
            token,
            jwt_secret,
            algorithms=[_ALGORITHM],
            audience=_AUDIENCE,
        )
        return decoded.get("scope") == _SCOPE and decoded.get("sub") == "admin"
    except jwt.PyJWTError:
        return False


async def require_admin_jwt(
    settings: Settings = Depends(get_settings),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    """FastAPI dependency — enforces a valid admin JWT in the Authorization header.

    Raises :class:`~app.errors.ProblemDetail` on any failure.
    Returns ``None`` on success (callers can ignore the return value).
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise ProblemDetail(
            status=401,
            code="missing_admin_token",
            title="Unauthorized",
            detail="Authorization: Bearer <admin_jwt> header is required.",
        )
    token = authorization[len("Bearer "):]
    if not verify_admin_jwt(token, settings.jwt_secret):
        raise ProblemDetail(
            status=403,
            code="bad_admin_token",
            title="Forbidden",
            detail="The supplied admin token is invalid or expired.",
        )
