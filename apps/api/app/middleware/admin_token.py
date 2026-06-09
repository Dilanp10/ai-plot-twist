"""Admin-token bearer verification — FastAPI dependency for admin endpoints.

Module 003 / Task T-016.

Usage::

    from fastapi import Depends
    from app.middleware.admin_token import verify_admin_token

    @router.post("/kill-switch")
    async def kill_switch(
        _: None = Depends(verify_admin_token),
        ...
    ) -> ...:
        ...

Error codes:
  admin_token_missing  503 — ADMIN_TOKEN env var is not set on the server.
  missing_admin_token  401 — Authorization header absent or not "Bearer …".
  bad_admin_token      403 — Token present but does not match ADMIN_TOKEN.
"""

from __future__ import annotations

import hmac

from fastapi import Depends, Header

from app.errors import ProblemDetail
from app.settings import Settings, get_settings

__all__ = ["verify_admin_token"]


async def verify_admin_token(
    settings: Settings = Depends(get_settings),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    """Verify ``Authorization: Bearer <ADMIN_TOKEN>``.

    Raises :class:`~app.errors.ProblemDetail` on any failure so the global
    ``problem_handler`` returns an RFC 7807 response.

    Returns ``None`` on success (the route handler ignores the return value).
    """
    # ── 1. Server config ────────────────────────────────────────────────────
    if not settings.admin_token:
        raise ProblemDetail(
            status=503,
            code="admin_token_missing",
            title="Server misconfigured",
            detail="ADMIN_TOKEN is not set on the server.",
        )

    # ── 2. Header presence and format ───────────────────────────────────────
    if not authorization or not authorization.startswith("Bearer "):
        raise ProblemDetail(
            status=401,
            code="missing_admin_token",
            title="Unauthorized",
            detail="Authorization: Bearer <ADMIN_TOKEN> header is required.",
        )

    # ── 3. Constant-time comparison (prevents timing attacks) ───────────────
    token = authorization[len("Bearer "):]
    if not hmac.compare_digest(token, settings.admin_token):
        raise ProblemDetail(
            status=403,
            code="bad_admin_token",
            title="Forbidden",
            detail="The supplied admin token is invalid.",
        )
