"""DeviceSecret — mint and verify device-bound secrets.

A device secret is a 32-byte cryptographically random value encoded as
URL-safe base64 (no padding).  The server stores only its SHA-256 hex
digest (64 chars), which matches the ``device_token`` column CHECK constraint.

API
---
    raw_b64url, hash_hex = mint()
    # raw_b64url → sent to the client once, never stored server-side
    # hash_hex   → persisted in users.device_token

    ok = verify(raw_b64url, stored_hash_hex)
    # constant-time comparison to prevent timing attacks
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode

_SECRET_BYTES: int = 32


def mint() -> tuple[str, str]:
    """Generate a fresh device secret.

    Returns
    -------
    tuple[str, str]
        ``(raw_b64url, sha256_hex)`` where:

        * *raw_b64url* — 43-char URL-safe base64 string (no padding),
          sent to the client in the ``device_secret`` response field.
        * *sha256_hex* — 64-char lowercase hex string, persisted as
          ``users.device_token``.
    """
    raw_bytes = secrets.token_bytes(_SECRET_BYTES)
    raw_b64url = urlsafe_b64encode(raw_bytes).rstrip(b"=").decode()
    sha256_hex = hashlib.sha256(raw_bytes).hexdigest()
    return raw_b64url, sha256_hex


def verify(raw_b64url: str, stored_hash_hex: str) -> bool:
    """Return ``True`` iff *raw_b64url* hashes to *stored_hash_hex*.

    Uses :func:`hmac.compare_digest` for constant-time comparison to
    prevent timing-based enumeration of stored hashes.

    Returns ``False`` (never raises) on any malformed input.
    """
    try:
        # Re-add the padding that mint() stripped before decoding.
        padding = "=" * (-len(raw_b64url) % 4)
        raw_bytes = urlsafe_b64decode(raw_b64url + padding)
        candidate_hex = hashlib.sha256(raw_bytes).hexdigest()
        return hmac.compare_digest(candidate_hex, stored_hash_hex.lower())
    except Exception:  # broad catch: invalid b64, wrong hash length, etc.
        return False
