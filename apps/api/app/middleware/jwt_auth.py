"""JWT authentication dependency for FastAPI routes.

Provides :func:`require_user`, a FastAPI ``Depends`` that:

1. Extracts ``Authorization: Bearer <token>`` from the request.
2. Verifies the JWT with :class:`~app.domain.jwt_service.JWTService`.
3. Looks up the user by ``sub`` (UUID) — cached for 60 s (LRU, 10 k slots).
4. Rejects banned users with HTTP 403.
5. Returns the :class:`~app.infra.users_repo.UserRow`.

Cache rationale: the JWT is verified cryptographically on every request, but
the DB round-trip to fetch user details is elided for up to 60 s.  Bans take
effect within one cache TTL.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.domain.jwt_service import JWTService
from app.infra.users_repo import UserRow, UsersRepo
from app.settings import get_settings

# ---------------------------------------------------------------------------
# LRU + TTL cache
# ---------------------------------------------------------------------------

class _TTLCache:
    """Thread-safe (within asyncio's single-threaded loop) LRU TTL cache."""

    def __init__(self, ttl_seconds: int = 60, maxsize: int = 10_000) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._maxsize = maxsize
        self._data: OrderedDict[UUID, tuple[datetime, UserRow]] = OrderedDict()

    def get(self, key: UUID) -> UserRow | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        ts, val = entry
        if datetime.now(UTC) - ts > self._ttl:
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return val

    def set(self, key: UUID, val: UserRow) -> None:
        if key in self._data:
            self._data.move_to_end(key)
        elif len(self._data) >= self._maxsize:
            self._data.popitem(last=False)  # evict LRU
        self._data[key] = (datetime.now(UTC), val)

    def invalidate(self, key: UUID) -> None:
        """Remove a key from the cache (e.g. after a ban)."""
        self._data.pop(key, None)


_user_cache: _TTLCache = _TTLCache()


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


async def require_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> UserRow:
    """FastAPI dependency: authenticate the request and return the user.

    Raises
    ------
    HTTPException(401)
        Missing / invalid / expired JWT.
    HTTPException(403)
        Valid JWT but the user is banned.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token requerido.")

    token = auth_header.removeprefix("Bearer ")
    settings = get_settings()
    claims = JWTService(settings.jwt_secret).verify(token)
    if claims is None:
        raise HTTPException(status_code=401, detail="Token inválido o expirado.")

    user = _user_cache.get(claims.sub)
    if user is None:
        user = await UsersRepo(session).get_by_public_id(claims.sub)
        if user is None:
            raise HTTPException(status_code=401, detail="Usuario no encontrado.")
        _user_cache.set(claims.sub, user)

    if user.is_banned:
        _user_cache.invalidate(user.public_id)
        raise HTTPException(status_code=403, detail="Usuario suspendido.")

    return user
