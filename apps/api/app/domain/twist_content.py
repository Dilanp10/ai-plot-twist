"""Twist content — normalize and validate plot-twist proposals.

Module 005 / Task T-002.

Pipeline applied in order:
  1. NFKC normalization (collapses compatibility characters, e.g. ﬁ→fi)
  2. Strip Unicode control / format / private-use / surrogate chars
     (categories Cc, Cf, Co, Cs). This removes null bytes, zero-width
     spaces, BOM, RTL overrides, and other invisible chars that would
     otherwise let users smuggle hidden state past the length check
     or spoof direction in the LLM-filter input.
  3. Strip leading/trailing whitespace.
  4. Validate length: MIN_LEN..MAX_LEN characters (after normalization).

Raises :exc:`ValueError` on any invalid input so callers never persist
a twist that would violate the DB CHECK constraint
``ck_twists_content_len`` (see migration 0007_twists).
"""

from __future__ import annotations

import unicodedata

MIN_LEN: int = 5
MAX_LEN: int = 280

# Unicode general categories that represent control / non-printable /
# direction-spoofing chars.
_STRIP_CATEGORIES: frozenset[str] = frozenset({"Cc", "Cf", "Co", "Cs"})


def _strip_invisible_chars(s: str) -> str:
    return "".join(ch for ch in s if unicodedata.category(ch) not in _STRIP_CATEGORIES)


def normalize(raw: str) -> str:
    """Return the normalized twist content, or raise :exc:`ValueError`.

    Parameters
    ----------
    raw:
        User-supplied twist content (any string).

    Returns
    -------
    str
        NFKC-normalized, invisible-char-stripped, trimmed content.

    Raises
    ------
    ValueError
        If the result is shorter than :data:`MIN_LEN` or longer than
        :data:`MAX_LEN` characters.
    """
    normalized = unicodedata.normalize("NFKC", raw)
    cleaned = _strip_invisible_chars(normalized)
    trimmed = cleaned.strip()

    length = len(trimmed)
    if length < MIN_LEN:
        raise ValueError(
            f"La idea es demasiado corta ({length} car.); mínimo {MIN_LEN}."
        )
    if length > MAX_LEN:
        raise ValueError(
            f"La idea es demasiado larga ({length} car.); máximo {MAX_LEN}."
        )

    return trimmed
