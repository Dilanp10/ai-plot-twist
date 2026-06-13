"""Unit tests: Spanish slur matcher.

Module 006 / Task T-007.

Coverage:
  - Curated list is non-empty and within the documented cap.
  - Positive matches for each curated entry.
  - Case-insensitive.
  - Word-boundary semantics (substring inside a larger word does NOT match).
  - Negative cases: clean content, accent-mismatched look-alikes that are
    NOT in the list, sentences that contain a slur-adjacent neutral word.
"""

from __future__ import annotations

import pytest

from app.domain.slur_list import matches_slur, slur_count

# ---------------------------------------------------------------------------
# Catalog sanity
# ---------------------------------------------------------------------------


def test_catalog_is_non_empty_and_under_cap() -> None:
    n = slur_count()
    assert n > 0
    # Spec / module docstring says ≤ 30 entries.
    assert n <= 30


# ---------------------------------------------------------------------------
# Positive matches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        "Sos un boludo",
        "qué pelotudo total",
        "BOLUDO en mayúsculas",
        "Hijo de puta total",
        "esa idea es una imbecilidad de imbécil",
        "es un trolo de campeonato",
    ],
)
def test_matches_known_slurs(content: str) -> None:
    assert matches_slur(content) is True


def test_matches_is_case_insensitive() -> None:
    assert matches_slur("BoLuDo") is True
    assert matches_slur("PELOTUDO") is True


# ---------------------------------------------------------------------------
# Word-boundary semantics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        # Substring inside a longer word — must NOT trigger.
        "El gatoperro vive en la cuadra",     # "gato" inside "gatoperro"
        "putorrespeto al canon",              # "puto" inside "putorrespeto"
        # Multi-word slur must match as exact phrase only; partial doesn't.
        "hijo de mamá querida",               # "hijo de puta" not present
    ],
)
def test_does_not_match_substrings_or_partial_phrases(content: str) -> None:
    assert matches_slur(content) is False


# ---------------------------------------------------------------------------
# Clean content
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        "Una idea creativa y bien escrita.",
        "El protagonista descubre un secreto.",
        "Termina con una pelea en el subte.",
        "",
        "   ",
        "Día 3 de la temporada — final feliz.",
    ],
)
def test_clean_content_does_not_match(content: str) -> None:
    assert matches_slur(content) is False
