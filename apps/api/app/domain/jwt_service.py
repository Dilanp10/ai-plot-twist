"""JWTService — issue and verify HS256 JWTs.

Tokens carry:
  sub  — user public_id (UUID, as string)
  aud  — "aiplottwist"
  iat  — issued-at (UTC)
  exp  — issued-at + 90 days

Verification accepts a configurable leeway (default 60 s) to tolerate
small clock skew between issuer and verifier.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt

JWT_ALGORITHM: str = "HS256"
JWT_AUDIENCE: str = "aiplottwist"
JWT_TTL_DAYS: int = 90


@dataclass(frozen=True)
class JWTClaims:
    """Decoded, validated claims extracted from a verified JWT."""

    sub: UUID       # user public_id
    exp: datetime   # expiry (UTC, tz-aware)


class JWTService:
    """Issue and verify application JWTs.

    Parameters
    ----------
    secret:
        The HMAC-SHA256 signing key (``settings.jwt_secret``).
    leeway_seconds:
        Seconds of clock-skew tolerance applied during ``verify()``.
        Defaults to 60.
    """

    def __init__(self, secret: str, leeway_seconds: int = 60) -> None:
        self._secret = secret
        self._leeway = timedelta(seconds=leeway_seconds)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def issue(self, user_public_id: UUID) -> tuple[str, datetime]:
        """Sign a new JWT for *user_public_id*.

        Returns
        -------
        tuple[str, datetime]
            ``(encoded_token, expiry_utc)``
        """
        now = datetime.now(UTC)
        exp = now + timedelta(days=JWT_TTL_DAYS)
        payload: dict[str, object] = {
            "sub": str(user_public_id),
            "aud": JWT_AUDIENCE,
            "iat": now,
            "exp": exp,
        }
        token: str = jwt.encode(payload, self._secret, algorithm=JWT_ALGORITHM)
        return token, exp

    def verify(self, token: str) -> JWTClaims | None:
        """Decode and validate *token*.

        Returns ``None`` on **any** failure: invalid signature, expired
        (outside leeway), wrong audience, malformed payload, etc.
        """
        try:
            decoded: dict[str, object] = jwt.decode(
                token,
                self._secret,
                algorithms=[JWT_ALGORITHM],
                audience=JWT_AUDIENCE,
                leeway=self._leeway,
            )
            sub_raw = decoded["sub"]
            exp_raw = decoded["exp"]
            if not isinstance(sub_raw, str) or not isinstance(exp_raw, (int, float)):
                return None
            return JWTClaims(
                sub=UUID(sub_raw),
                exp=datetime.fromtimestamp(float(exp_raw), tz=UTC),
            )
        except (jwt.PyJWTError, ValueError, KeyError):
            return None
