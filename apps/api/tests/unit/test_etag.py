"""Unit tests: chapter ETag derivation.

Module 004 / Task T-003.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

from app.domain.etag import derive_etag

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PID = UUID("9f3a3b5f-0000-4000-8000-000000007e2c")
_STATE = "RECEPCION_IDEAS"
_RELEASED_AT = datetime(2026, 6, 8, 15, 0, 0, tzinfo=UTC)

_ART = timezone(timedelta(hours=-3))


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_same_inputs_produce_same_etag() -> None:
    a = derive_etag(_PID, _STATE, _RELEASED_AT)
    b = derive_etag(_PID, _STATE, _RELEASED_AT)
    assert a == b


# ---------------------------------------------------------------------------
# Sensitivity — each input must influence the output
# ---------------------------------------------------------------------------


def test_changes_with_chapter_public_id() -> None:
    other = UUID("00000000-0000-4000-8000-000000000001")
    assert derive_etag(_PID, _STATE, _RELEASED_AT) != derive_etag(other, _STATE, _RELEASED_AT)


def test_changes_with_cycle_state() -> None:
    assert derive_etag(_PID, "VOTACION", _RELEASED_AT) != derive_etag(_PID, _STATE, _RELEASED_AT)


def test_changes_with_released_at() -> None:
    later = _RELEASED_AT + timedelta(seconds=1)
    assert derive_etag(_PID, _STATE, later) != derive_etag(_PID, _STATE, _RELEASED_AT)


# ---------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------


def test_format_is_16_lowercase_hex() -> None:
    etag = derive_etag(_PID, _STATE, _RELEASED_AT)
    assert len(etag) == 16
    assert all(c in "0123456789abcdef" for c in etag)


def test_no_quotes_in_output() -> None:
    """RFC 7232 quoting is the HTTP layer's responsibility, not ours."""
    etag = derive_etag(_PID, _STATE, _RELEASED_AT)
    assert '"' not in etag
    assert "'" not in etag


# ---------------------------------------------------------------------------
# TZ normalization — equivalent instants in different zones → same ETag
# ---------------------------------------------------------------------------


def test_same_instant_in_art_and_utc_produces_same_etag() -> None:
    """``released_at`` is normalized to UTC before hashing."""
    in_utc = datetime(2026, 6, 8, 15, 0, 0, tzinfo=UTC)
    in_art = datetime(2026, 6, 8, 12, 0, 0, tzinfo=_ART)
    assert derive_etag(_PID, _STATE, in_utc) == derive_etag(_PID, _STATE, in_art)


def test_naive_datetime_is_assumed_utc() -> None:
    aware = datetime(2026, 6, 8, 15, 0, 0, tzinfo=UTC)
    naive = datetime(2026, 6, 8, 15, 0, 0)  # no tzinfo
    assert derive_etag(_PID, _STATE, aware) == derive_etag(_PID, _STATE, naive)
