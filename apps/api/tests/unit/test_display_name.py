"""Unit tests: display_name normalizer.

Module 002 / Task T-007.

Coverage:
  - NFKC normalization collapses compatibility chars
  - Control characters are stripped
  - Leading/trailing whitespace is trimmed
  - Valid edge cases (2 chars, 24 chars) accepted
  - Too short (empty, 1 char after trim) raises ValueError
  - Too long (25+ chars) raises ValueError
"""

from __future__ import annotations

import pytest

from app.domain.display_name import normalize

# ---------------------------------------------------------------------------
# Valid names
# ---------------------------------------------------------------------------


def test_simple_ascii_name() -> None:
    assert normalize("Dilan") == "Dilan"


def test_minimum_length() -> None:
    assert normalize("AB") == "AB"


def test_maximum_length() -> None:
    name = "A" * 24
    assert normalize(name) == name


def test_leading_trailing_whitespace_trimmed() -> None:
    assert normalize("  Lucía  ") == "Lucía"


def test_nfkc_collapses_compatibility_chars() -> None:
    # LATIN SMALL LIGATURE fi (U+FB01) → "fi"
    assert normalize("ﬁn") == "fin"


def test_nfkc_fullwidth_digits_normalized() -> None:
    # FULLWIDTH DIGIT ONE (U+FF11) normalizes to ASCII '1' via NFKC
    fullwidth_one = "\uff11"
    assert normalize("user" + fullwidth_one) == "user1"


def test_accented_chars_preserved() -> None:
    # é, ñ, ü are valid NFKC and should pass through
    assert normalize("Renée") == "Renée"


def test_name_with_spaces_in_middle() -> None:
    assert normalize("Juan Carlos") == "Juan Carlos"


# ---------------------------------------------------------------------------
# Control character stripping
# ---------------------------------------------------------------------------


def test_strips_null_byte() -> None:
    # \x00 is Cc (control char) — stripped, result must still be ≥ 2 chars
    assert normalize("AB\x00") == "AB"


def test_strips_zero_width_space() -> None:
    # U+200B ZERO WIDTH SPACE is category Cf — stripped
    assert normalize("AB​") == "AB"


def test_strips_bom() -> None:
    # U+FEFF BOM is category Cf
    assert normalize("﻿Hola") == "Hola"


# ---------------------------------------------------------------------------
# Too short → ValueError
# ---------------------------------------------------------------------------


def test_empty_string_raises() -> None:
    with pytest.raises(ValueError):
        normalize("")


def test_one_char_raises() -> None:
    with pytest.raises(ValueError):
        normalize("A")


def test_only_whitespace_raises() -> None:
    with pytest.raises(ValueError):
        normalize("   ")


def test_only_control_chars_raises() -> None:
    # After stripping Cf chars the result is empty → too short
    with pytest.raises(ValueError):
        normalize("​​​")


def test_one_real_char_after_trim_raises() -> None:
    with pytest.raises(ValueError):
        normalize("  X  ")


# ---------------------------------------------------------------------------
# Too long → ValueError
# ---------------------------------------------------------------------------


def test_25_chars_raises() -> None:
    with pytest.raises(ValueError):
        normalize("A" * 25)


def test_exactly_25_chars_raises() -> None:
    with pytest.raises(ValueError):
        normalize("B" * 25)


# ---------------------------------------------------------------------------
# Error message content (Spanish UI strings)
# ---------------------------------------------------------------------------


def test_too_short_error_mentions_minimo() -> None:
    with pytest.raises(ValueError, match="mínimo"):
        normalize("X")


def test_too_long_error_mentions_maximo() -> None:
    with pytest.raises(ValueError, match="máximo"):
        normalize("X" * 25)

