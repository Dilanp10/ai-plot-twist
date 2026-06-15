"""Push notification payload composition (FR-010).

Module 011 / Task T-005.

The PWA's service worker (T-013) receives whatever this module emits,
parses it as JSON, and calls ``self.registration.showNotification``.
The payload shape is fixed by FR-010:

    {
      "title": "AI Plot Twist — Día 8",
      "body":  "Hoy: Lo que había detrás del espejo",
      "icon":  "/icons/icon-192.png",
      "badge": "/icons/badge-72.png",
      "tag":   "chapter-<public_id>",
      "data":  { "chapter_public_id": "...", "url": "/today" }
    }

The ``tag`` field is what dedupes notifications on the device: if the
user has two subscriptions for the same browser (work + personal
profile, e.g.), both pushes fire but the OS collapses them into a
single banner because ``tag`` matches.

Title + body are clamped to **200 characters total** — push services
silently truncate long payloads (FCM ≤ 4 KB, but the visible region
is far smaller), so we enforce the soft cap at compose time and log
when the source chapter forces truncation.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

# FR-010 — total visible characters across title + body.
TITLE_BODY_MAX_CHARS = 200

# Static asset paths used by the SW's showNotification call. Names match
# the PWA's manifest icons (vite-plugin-pwa T-004 writes these into
# dist/icons/).
_ICON_URL = "/icons/icon-192.png"
_BADGE_URL = "/icons/badge-72.png"


# ---------------------------------------------------------------------------
# Title + body builders
# ---------------------------------------------------------------------------


def _format_title(day_index: int) -> str:
    return f"AI Plot Twist — Día {day_index}"


def _format_body(chapter_title: str) -> str:
    return f"Hoy: {chapter_title}"


def _clamp_title_body(title: str, body: str) -> tuple[str, str]:
    """Clamp the combined title + body to ``TITLE_BODY_MAX_CHARS``.

    Preserves the title verbatim (short by construction) and truncates
    the body with an ellipsis when needed. When the title alone exceeds
    the cap (shouldn't happen with the static template, but defensive),
    truncate the title instead and emit an empty body.
    """
    total = len(title) + len(body)
    if total <= TITLE_BODY_MAX_CHARS:
        return title, body

    over = total - TITLE_BODY_MAX_CHARS
    if len(body) > over + 1:  # +1 to leave room for the ellipsis
        new_body = body[: len(body) - over - 1].rstrip() + "…"
        logger.warning(
            "push_payload_clamped reason=body_too_long over=%d kept=%d",
            over,
            len(new_body),
        )
        return title, new_body

    # Title alone over the cap — keep the first TITLE_BODY_MAX_CHARS - 1
    # chars and drop the body entirely.
    new_title = title[: TITLE_BODY_MAX_CHARS - 1].rstrip() + "…"
    logger.warning(
        "push_payload_clamped reason=title_too_long kept=%d",
        len(new_title),
    )
    return new_title, ""


# ---------------------------------------------------------------------------
# Public composers
# ---------------------------------------------------------------------------


def compose_chapter_notification(
    *,
    chapter_public_id: UUID,
    chapter_title: str,
    day_index: int,
) -> dict[str, Any]:
    """Build the FR-010 payload for a chapter ESTRENO push.

    Parameters
    ----------
    chapter_public_id:
        The new chapter's public UUID — embedded in ``data`` so the
        SW's notification-click handler can deep-link to that chapter
        when 010 T-013 supports it.
    chapter_title:
        Human-readable chapter title (Spanish). Truncated when the
        title + body combination exceeds ``TITLE_BODY_MAX_CHARS``.
    day_index:
        The chapter's day index in its season — used in the title.
    """
    title = _format_title(day_index)
    body = _format_body(chapter_title)
    title, body = _clamp_title_body(title, body)
    public_str = str(chapter_public_id)
    return {
        "title": title,
        "body": body,
        "icon": _ICON_URL,
        "badge": _BADGE_URL,
        "tag": f"chapter-{public_str}",
        "data": {
            "chapter_public_id": public_str,
            "url": "/today",
        },
    }


def compose_test_notification() -> dict[str, Any]:
    """Build a fixed payload for the admin test endpoint (T-009).

    Same shape as the chapter notification so SW + tag-dedup paths get
    real coverage during a deploy smoke. The tag uses a static value so
    a re-fire from the admin endpoint coalesces into a single banner
    even on devices with multiple subscriptions.
    """
    return {
        "title": "AI Plot Twist — prueba",
        "body": "Push de prueba enviado desde admin.",
        "icon": _ICON_URL,
        "badge": _BADGE_URL,
        "tag": "admin-test",
        "data": {"url": "/today"},
    }
