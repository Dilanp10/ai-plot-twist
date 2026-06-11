"""Unit tests: twist_content normalizer.

Module 005 / Task T-002.

Coverage:
  - NFKC normalization collapses compatibility chars
  - Cc/Cf/Co/Cs (control/format/private/surrogate) chars are stripped
  - Leading/trailing whitespace is trimmed
  - Valid edge cases (5 chars, 280 chars) accepted
  - Too short (empty, 4 chars, only-whitespace, only-zero-width) raises
  - Too long (281+ chars) raises ValueError
  - Emojis are preserved (category So, not in strip set)
  - RTL overrides and zero-width chars are stripped (anti-spoofing)
  - Spanish UI error messages mention "mínimo" / "máximo"
"""

from __future__ import annotations

import pytest

from app.domain.twist_content import MAX_LEN, normalize

# ---------------------------------------------------------------------------
# Valid content
# ---------------------------------------------------------------------------


def test_simple_ascii() -> None:
    assert normalize("Hola mundo") == "Hola mundo"


def test_minimum_length() -> None:
    assert normalize("12345") == "12345"


def test_maximum_length() -> None:
    content = "A" * MAX_LEN
    assert normalize(content) == content


def test_leading_trailing_whitespace_trimmed() -> None:
    assert normalize("   Lucía vuelve y miente.   ") == "Lucía vuelve y miente."


def test_nfkc_collapses_compatibility_chars() -> None:
    # LATIN SMALL LIGATURE fi (U+FB01) → "fi"
    assert normalize("ﬁnal feliz no") == "final feliz no"


def test_nfkc_fullwidth_digits_normalized() -> None:
    # FULLWIDTH DIGIT ONE (U+FF11) normalizes to ASCII '1' via NFKC.
    fullwidth_one = "１"  # noqa: RUF001
    assert normalize(f"cap{fullwidth_one}tulo") == "cap1tulo"


def test_accented_chars_preserved() -> None:
    assert normalize("Renée toca el piñón.") == "Renée toca el piñón."


def test_internal_spaces_preserved() -> None:
    assert normalize("Juan  Carlos  vuelve") == "Juan  Carlos  vuelve"


def test_emojis_preserved() -> None:
    # 🔥 (U+1F525) is category So (Symbol other) — must NOT be stripped.
    assert normalize("Final 🔥 explosivo!") == "Final 🔥 explosivo!"


# ---------------------------------------------------------------------------
# Invisible / control char stripping
# ---------------------------------------------------------------------------


def test_strips_null_byte() -> None:
    # \x00 is Cc
    assert normalize("Cap\x00ítulo loco") == "Capítulo loco"


def test_strips_zero_width_space() -> None:
    # U+200B ZERO WIDTH SPACE is Cf
    assert normalize("Hola​mundo entero") == "Holamundo entero"


def test_strips_bom() -> None:
    # U+FEFF BOM is Cf
    assert normalize("﻿Hola mundo loco") == "Hola mundo loco"


def test_strips_rtl_override() -> None:
    # U+202E RIGHT-TO-LEFT OVERRIDE is Cf — anti-spoofing defense.
    assert normalize("Hola‮mundo entero") == "Holamundo entero"


# ---------------------------------------------------------------------------
# Too short → ValueError
# ---------------------------------------------------------------------------


def test_empty_string_raises() -> None:
    with pytest.raises(ValueError):
        normalize("")


def test_four_chars_raises() -> None:
    with pytest.raises(ValueError):
        normalize("Hola")


def test_only_whitespace_raises() -> None:
    with pytest.raises(ValueError):
        normalize("        ")


def test_only_zero_width_chars_raises() -> None:
    # After stripping Cf the result is empty → too short.
    with pytest.raises(ValueError):
        normalize("​​​​​​")


# ---------------------------------------------------------------------------
# Too long → ValueError
# ---------------------------------------------------------------------------


def test_281_chars_raises() -> None:
    with pytest.raises(ValueError):
        normalize("A" * (MAX_LEN + 1))


# ---------------------------------------------------------------------------
# Error message content (Spanish UI strings)
# ---------------------------------------------------------------------------


def test_too_short_error_mentions_minimo() -> None:
    with pytest.raises(ValueError, match="mínimo"):
        normalize("Hi")


def test_too_long_error_mentions_maximo() -> None:
    with pytest.raises(ValueError, match="máximo"):
        normalize("A" * (MAX_LEN + 1))
