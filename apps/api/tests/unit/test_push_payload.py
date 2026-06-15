"""Unit tests: push payload composition (T-005).

Coverage:
  1. compose_chapter_notification produces the exact FR-010 shape.
  2. tag embeds the chapter public_id so device-side dedup works.
  3. data carries chapter_public_id + url for the SW click handler.
  4. Title + body ≤ 200 chars on a normal chapter.
  5. Long chapter title triggers body truncation with ellipsis.
  6. Pathological long title gets clamped, body drops to empty.
  7. compose_test_notification has the static admin-test tag.
"""

from __future__ import annotations

from uuid import UUID

from app.domain.push_payload import (
    TITLE_BODY_MAX_CHARS,
    compose_chapter_notification,
    compose_test_notification,
)

_PUBLIC_ID = UUID("12345678-1234-5678-1234-567812345678")


# ---------------------------------------------------------------------------
# compose_chapter_notification
# ---------------------------------------------------------------------------


def test_compose_chapter_notification_shape() -> None:
    payload = compose_chapter_notification(
        chapter_public_id=_PUBLIC_ID,
        chapter_title="Lo que había detrás del espejo",
        day_index=8,
    )
    assert payload["title"] == "AI Plot Twist — Día 8"
    assert payload["body"] == "Hoy: Lo que había detrás del espejo"
    assert payload["icon"] == "/icons/icon-192.png"
    assert payload["badge"] == "/icons/badge-72.png"


def test_compose_chapter_notification_tag_embeds_public_id() -> None:
    payload = compose_chapter_notification(
        chapter_public_id=_PUBLIC_ID,
        chapter_title="A",
        day_index=1,
    )
    assert payload["tag"] == f"chapter-{_PUBLIC_ID}"


def test_compose_chapter_notification_data_routes_to_today() -> None:
    payload = compose_chapter_notification(
        chapter_public_id=_PUBLIC_ID,
        chapter_title="A",
        day_index=1,
    )
    assert payload["data"] == {
        "chapter_public_id": str(_PUBLIC_ID),
        "url": "/today",
    }


def test_compose_chapter_notification_under_cap_normal() -> None:
    payload = compose_chapter_notification(
        chapter_public_id=_PUBLIC_ID,
        chapter_title="Un título normal",
        day_index=1,
    )
    assert (
        len(payload["title"]) + len(payload["body"]) <= TITLE_BODY_MAX_CHARS
    )


# ---------------------------------------------------------------------------
# Clamping
# ---------------------------------------------------------------------------


def test_long_chapter_title_truncates_body_with_ellipsis() -> None:
    long_title = "A" * 250  # forces body well over the cap
    payload = compose_chapter_notification(
        chapter_public_id=_PUBLIC_ID,
        chapter_title=long_title,
        day_index=1,
    )
    total = len(payload["title"]) + len(payload["body"])
    assert total <= TITLE_BODY_MAX_CHARS
    assert payload["body"].endswith("…")
    # Title was preserved verbatim.
    assert payload["title"] == "AI Plot Twist — Día 1"


def test_body_exactly_at_limit_kept_verbatim() -> None:
    # Title "AI Plot Twist — Día 1" is 21 chars. Body should make total = 200.
    base_title_len = len("AI Plot Twist — Día 1")
    chapter_title = "C" * (TITLE_BODY_MAX_CHARS - base_title_len - len("Hoy: "))
    payload = compose_chapter_notification(
        chapter_public_id=_PUBLIC_ID,
        chapter_title=chapter_title,
        day_index=1,
    )
    assert (
        len(payload["title"]) + len(payload["body"]) == TITLE_BODY_MAX_CHARS
    )
    # No truncation happened.
    assert not payload["body"].endswith("…")


# ---------------------------------------------------------------------------
# compose_test_notification
# ---------------------------------------------------------------------------


def test_compose_test_notification_static_tag() -> None:
    payload = compose_test_notification()
    assert payload["tag"] == "admin-test"
    assert payload["title"].startswith("AI Plot Twist")
    assert "prueba" in payload["title"].lower()
    assert payload["data"] == {"url": "/today"}
