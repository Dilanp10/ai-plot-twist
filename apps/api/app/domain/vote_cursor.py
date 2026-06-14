"""Opaque cursor for vote-feed pagination.

Module 007 / Task T-003.

A cursor is a base64-urlsafe-encoded JSON object capturing the sort mode,
the last index served, and (optionally) the last sort value. The sort
mode is round-tripped so the server can detect a cross-sort mismatch and
return 422 ``cursor_invalid`` instead of silently producing a confusing
page (spec edge case + R-V3).

Pure: no DB, no HTTP. Encoded form is opaque to clients (Gate 9): clients
should treat it as a string token.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

Sort = Literal["random", "recent", "hot"]
_VALID_SORTS: frozenset[str] = frozenset(("random", "recent", "hot"))


class CursorInvalid(ValueError):
    """Raised when a cursor string cannot be decoded into a valid :class:`Cursor`."""


@dataclass(frozen=True)
class Cursor:
    """Pagination cursor for the vote-feed.

    ``sort`` matches the original request's sort mode. ``last_position``
    is the index *after* the last served item in the already-sorted list
    (so the next page starts at ``items[last_position:]``).
    ``last_sort_value`` is an optional secondary check — for ``recent``
    it's the ISO timestamp, for ``hot`` it's the vote_count.  For
    ``random`` it stays ``None`` (position alone is sufficient because
    the seed is stable).
    """

    sort: Sort
    last_position: int
    last_sort_value: int | str | None


def encode(c: Cursor) -> str:
    """Encode a cursor as a base64-urlsafe (no-padding) JSON token."""
    payload = json.dumps(asdict(c), separators=(",", ":"), sort_keys=True)
    raw = payload.encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode(s: str) -> Cursor:
    """Decode a base64-urlsafe JSON cursor.

    Raises :class:`CursorInvalid` on:
      - non-ASCII / non-base64 input,
      - JSON that isn't an object with the expected keys,
      - an unknown ``sort`` value,
      - a negative ``last_position``,
      - a ``last_sort_value`` of an unsupported type.
    """
    if not s:
        raise CursorInvalid("empty cursor")
    try:
        padded = s + "=" * (-len(s) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise CursorInvalid("cursor is not valid base64") from exc

    try:
        obj: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CursorInvalid("cursor payload is not valid JSON") from exc

    if not isinstance(obj, dict):
        raise CursorInvalid("cursor payload is not a JSON object")

    expected_keys = {"sort", "last_position", "last_sort_value"}
    if set(obj.keys()) != expected_keys:
        raise CursorInvalid(
            f"cursor payload keys mismatch: expected {expected_keys}, got {set(obj.keys())}"
        )

    sort = obj["sort"]
    if sort not in _VALID_SORTS:
        raise CursorInvalid(f"unknown sort: {sort!r}")

    last_position = obj["last_position"]
    if not isinstance(last_position, int) or isinstance(last_position, bool):
        raise CursorInvalid("last_position must be an int")
    if last_position < 0:
        raise CursorInvalid("last_position must be non-negative")

    last_sort_value = obj["last_sort_value"]
    if last_sort_value is not None and not isinstance(last_sort_value, (int, str)):
        raise CursorInvalid("last_sort_value must be int, str, or null")
    if isinstance(last_sort_value, bool):
        raise CursorInvalid("last_sort_value must not be bool")

    return Cursor(
        sort=sort,
        last_position=last_position,
        last_sort_value=last_sort_value,
    )
