"""ETag derivation for chapter responses.

Module 004 / Task T-003.

The ETag is the canonical fingerprint of a chapter response: it changes exactly
when the user-visible content of ``/chapters/today`` or ``/chapters/{id}`` does.

Per research R-005, the source tuple is
``(chapter.public_id, cycle.state, chapter.released_at)``. The hash is SHA-256
truncated to the first 16 lowercase hex chars — 64 bits of collision resistance,
sufficient for a finite chapter set (≤ ~30 per season) and a small audience.

``released_at`` is normalized to UTC ISO-8601 before hashing so that callers may
pass any tz-aware datetime (e.g. ART) without producing a different ETag for the
same instant.

The returned string is **bare** (no surrounding quotes). The HTTP layer adds the
quotes when emitting the ``ETag`` response header (per RFC 7232 §2.3).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID

_ETAG_HEX_LEN = 16


def derive_etag(
    chapter_public_id: UUID,
    cycle_state: str,
    released_at: datetime,
) -> str:
    """Return a stable 16-char lowercase hex ETag for the given chapter+state.

    Parameters
    ----------
    chapter_public_id:
        The chapter's public UUID. Same chapter → same first component.
    cycle_state:
        The current FSM state string (e.g. ``"RECEPCION_IDEAS"``). Changes when
        the cycle advances, invalidating PWA caches per spec FR-010.
    released_at:
        The chapter's release timestamp. Module 008 may re-publish a chapter
        with a corrected manifest; bumping ``released_at`` invalidates this
        ETag without requiring a new ``public_id``.

    Returns
    -------
    str
        16 lowercase hex characters. No surrounding quotes — the HTTP layer
        adds them.
    """
    released_utc = (
        released_at.astimezone(UTC) if released_at.tzinfo else released_at.replace(tzinfo=UTC)
    )
    payload = "|".join([str(chapter_public_id), cycle_state, released_utc.isoformat()])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:_ETAG_HEX_LEN]
