"""Unit tests: vote_cursor.

Module 007 / Task T-003.

Coverage:
  - Round-trip encode → decode is identity for all sort modes.
  - Encoded form is base64-urlsafe (no padding, no '+'/'/').
  - decode raises CursorInvalid on:
    - empty input,
    - non-base64 garbage,
    - valid base64 of non-JSON,
    - JSON array / scalar (not object),
    - missing or extra keys,
    - unknown sort,
    - negative last_position,
    - bool last_position (Python bool is a subclass of int — defensive check),
    - bool last_sort_value,
    - non-int/str last_sort_value.
"""

from __future__ import annotations

import base64
import json

import pytest

from app.domain.vote_cursor import Cursor, CursorInvalid, decode, encode

# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_roundtrip_random() -> None:
    c = Cursor(sort="random", last_position=25, last_sort_value=None)
    assert decode(encode(c)) == c


def test_roundtrip_recent_with_iso_value() -> None:
    c = Cursor(
        sort="recent",
        last_position=10,
        last_sort_value="2026-06-13T12:00:00+00:00",
    )
    assert decode(encode(c)) == c


def test_roundtrip_hot_with_int_value() -> None:
    c = Cursor(sort="hot", last_position=50, last_sort_value=7)
    assert decode(encode(c)) == c


def test_encoded_form_is_url_safe_and_unpadded() -> None:
    """Output uses '-_' alphabet (urlsafe) and strips '=' padding."""
    s = encode(Cursor(sort="random", last_position=0, last_sort_value=None))
    assert "=" not in s
    assert "+" not in s
    assert "/" not in s
    # Should be ASCII
    s.encode("ascii")


# ---------------------------------------------------------------------------
# decode error paths
# ---------------------------------------------------------------------------


def test_decode_empty_raises() -> None:
    with pytest.raises(CursorInvalid, match="empty"):
        decode("")


def test_decode_invalid_base64_raises() -> None:
    with pytest.raises(CursorInvalid, match="base64"):
        decode("!!!not base64@@@")


def test_decode_valid_base64_but_not_json_raises() -> None:
    s = base64.urlsafe_b64encode(b"\xff\xfe\xfd not json").rstrip(b"=").decode("ascii")
    with pytest.raises(CursorInvalid):
        decode(s)


def test_decode_json_array_not_object_raises() -> None:
    s = base64.urlsafe_b64encode(b"[1,2,3]").rstrip(b"=").decode("ascii")
    with pytest.raises(CursorInvalid, match="object"):
        decode(s)


def test_decode_missing_key_raises() -> None:
    payload = json.dumps({"sort": "random", "last_position": 0}).encode()
    s = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    with pytest.raises(CursorInvalid, match="keys"):
        decode(s)


def test_decode_extra_key_raises() -> None:
    payload = json.dumps(
        {
            "sort": "random",
            "last_position": 0,
            "last_sort_value": None,
            "extra": "x",
        }
    ).encode()
    s = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    with pytest.raises(CursorInvalid, match="keys"):
        decode(s)


def test_decode_unknown_sort_raises() -> None:
    payload = json.dumps(
        {"sort": "explosive", "last_position": 0, "last_sort_value": None}
    ).encode()
    s = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    with pytest.raises(CursorInvalid, match="sort"):
        decode(s)


def test_decode_negative_last_position_raises() -> None:
    payload = json.dumps(
        {"sort": "random", "last_position": -1, "last_sort_value": None}
    ).encode()
    s = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    with pytest.raises(CursorInvalid, match="non-negative"):
        decode(s)


def test_decode_bool_last_position_raises() -> None:
    """Python ``bool`` is an ``int`` subclass — defensive isinstance check."""
    payload = json.dumps(
        {"sort": "random", "last_position": True, "last_sort_value": None}
    ).encode()
    s = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    with pytest.raises(CursorInvalid, match="int"):
        decode(s)


def test_decode_bool_last_sort_value_raises() -> None:
    payload = json.dumps(
        {"sort": "hot", "last_position": 1, "last_sort_value": True}
    ).encode()
    s = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    with pytest.raises(CursorInvalid, match="bool"):
        decode(s)


def test_decode_float_last_sort_value_raises() -> None:
    payload = json.dumps(
        {"sort": "hot", "last_position": 1, "last_sort_value": 1.5}
    ).encode()
    s = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    with pytest.raises(CursorInvalid):
        decode(s)


# ---------------------------------------------------------------------------
# Cursor dataclass immutability
# ---------------------------------------------------------------------------


def test_cursor_is_frozen() -> None:
    c = Cursor(sort="random", last_position=0, last_sort_value=None)
    with pytest.raises(Exception):  # noqa: B017 - dataclass raises FrozenInstanceError
        c.last_position = 5  # type: ignore[misc]
