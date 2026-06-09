"""Unit tests: DeviceSecret helpers.

Module 002 / Task T-006.

Coverage:
  - mint() returns (raw_b64url, sha256_hex) with correct lengths / format
  - mint() generates unique values each call
  - verify() roundtrip succeeds
  - verify() rejects wrong raw
  - verify() rejects tampered hash
  - verify() returns False on garbage input (never raises)
"""

from __future__ import annotations

from app.domain.device_secret import mint, verify

# ---------------------------------------------------------------------------
# mint()
# ---------------------------------------------------------------------------


def test_mint_returns_two_strings() -> None:
    raw, digest = mint()
    assert isinstance(raw, str)
    assert isinstance(digest, str)


def test_mint_raw_is_43_chars() -> None:
    """32 bytes → 43 URL-safe base64 chars (no padding)."""
    raw, _ = mint()
    assert len(raw) == 43


def test_mint_raw_is_urlsafe_base64() -> None:
    """No standard base64 padding or forbidden chars."""
    raw, _ = mint()
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    assert all(c in allowed for c in raw), f"Caracteres inválidos en: {raw!r}"
    assert "=" not in raw


def test_mint_hash_is_64_hex_chars() -> None:
    """SHA-256 hex digest is always 64 lowercase hex chars."""
    _, digest = mint()
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_mint_generates_unique_pairs() -> None:
    pairs = {mint() for _ in range(100)}
    assert len(pairs) == 100  # no collisions in 100 tries


# ---------------------------------------------------------------------------
# verify()
# ---------------------------------------------------------------------------


def test_verify_roundtrip() -> None:
    raw, digest = mint()
    assert verify(raw, digest) is True


def test_verify_wrong_raw_returns_false() -> None:
    _raw, digest = mint()
    other_raw, _ = mint()
    assert verify(other_raw, digest) is False


def test_verify_tampered_hash_returns_false() -> None:
    raw, digest = mint()
    # Flip first hex char
    bad_first = "f" if digest[0] != "f" else "0"
    assert verify(raw, bad_first + digest[1:]) is False


def test_verify_hash_case_insensitive() -> None:
    """stored_hash_hex may be upper-case (future DB import scenario)."""
    raw, digest = mint()
    assert verify(raw, digest.upper()) is True


def test_verify_empty_raw_returns_false() -> None:
    _, digest = mint()
    assert verify("", digest) is False


def test_verify_empty_hash_returns_false() -> None:
    raw, _ = mint()
    assert verify(raw, "") is False


def test_verify_garbage_never_raises() -> None:
    assert verify("!!not-base64!!", "not-a-hash") is False
    assert verify("\x00\x01\x02", "abc") is False
