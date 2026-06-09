"""DisplayName — normalize and validate user display names.

Pipeline applied in order:
  1. NFKC normalization (collapses compatibility characters, e.g. ﬁ→fi)
  2. Strip ASCII and Unicode control characters (categories Cc, Cf, Co, Cs)
  3. Strip leading/trailing whitespace
  4. Validate length: 2-24 characters (after normalization)

Raises :exc:`ValueError` on any invalid input so callers never persist
a name that would violate the DB CHECK constraint.
"""

from __future__ import annotations

import unicodedata

_MIN_LEN: int = 2
_MAX_LEN: int = 24

# Unicode general categories that represent control / non-printable chars.
_CONTROL_CATEGORIES: frozenset[str] = frozenset({"Cc", "Cf", "Co", "Cs"})


def _strip_control_chars(s: str) -> str:
    return "".join(ch for ch in s if unicodedata.category(ch) not in _CONTROL_CATEGORIES)


def normalize(raw: str) -> str:
    """Return the normalized display name, or raise :exc:`ValueError`.

    Parameters
    ----------
    raw:
        User-supplied display name (any string).

    Returns
    -------
    str
        NFKC-normalized, control-char-stripped, trimmed name.

    Raises
    ------
    ValueError
        If the result is shorter than 2 or longer than 24 characters.
    """
    normalized = unicodedata.normalize("NFKC", raw)
    cleaned = _strip_control_chars(normalized)
    trimmed = cleaned.strip()

    length = len(trimmed)
    if length < _MIN_LEN:
        raise ValueError(
            f"El nombre de usuario es demasiado corto "
            f"({length} car.); mínimo {_MIN_LEN}."
        )
    if length > _MAX_LEN:
        raise ValueError(
            f"El nombre de usuario es demasiado largo "
            f"({length} car.); máximo {_MAX_LEN}."
        )

    return trimmed
