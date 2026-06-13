"""Spanish slur post-filter for the director's approved-twist pipeline.

Module 006 / Task T-007.

The director LLM is the first line of defense against offensive content;
this module is the second (Gate 9 defense-in-depth, FR-010). After the
LLM tags a verdict as ``approved``, the filter still re-checks the
content against a curated list of unambiguous Spanish slurs. On match,
the service (T-010) overrides the verdict to ``rejected_offensive``
with reason ``"Post-filter: contenido inadecuado."`` and emits a
``slur_override_applied`` structured log.

Design:
  - ≤ 30 entries, curated by the PO. The list is intentionally narrow:
    only words that are unambiguously slurs in River-Plate Spanish,
    with no legitimate non-pejorative use.
  - Case-insensitive matching.
  - Word boundaries (``\\b``) so the regex matches whole words and not
    substrings (e.g. ``"asno"`` should NOT trigger on ``"casona"``).
  - Compiled once at import.

The matcher returns a bool — callers don't need to know which entry
fired. Tests pin the boundary semantics so any future PR adding entries
inherits the same correctness contract.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Curated list — MVP. Additions require a PR and PO sign-off.
# Keep alphabetical so duplicates are obvious in review.
# Entries are lower-case; the compiled regex matches case-insensitively.
# ---------------------------------------------------------------------------

_SLURS: tuple[str, ...] = (
    "boludo",       # placeholder — common rioplatense insult; tunable
    "forro",
    "garca",
    "gato",         # context-dependent; PO may remove if too noisy
    "hijo de puta",
    "imbecil",
    "imbécil",
    "mogolico",
    "mogólico",
    "pelotudo",
    "puto",
    "retrasado",
    "subnormal",
    "tarado",
    "trolo",
)


# ---------------------------------------------------------------------------
# Compiled matcher
# ---------------------------------------------------------------------------


def _build_pattern(entries: tuple[str, ...]) -> re.Pattern[str]:
    """Compile a case-insensitive regex with word boundaries.

    ``\\b`` is Unicode-aware in Python's ``re`` when the pattern is a
    str and not a bytes object, so accented letters count as word chars.
    Multi-word entries (e.g. ``"hijo de puta"``) escape their inner
    spaces so they only match as exact phrases.
    """
    if not entries:
        # An empty pattern would match every position. Force a no-op.
        return re.compile(r"(?!)")
    escaped = [re.escape(e) for e in entries]
    pattern = r"\b(?:" + "|".join(escaped) + r")\b"
    return re.compile(pattern, re.IGNORECASE | re.UNICODE)


_PATTERN: re.Pattern[str] = _build_pattern(_SLURS)


def matches_slur(content: str) -> bool:
    """Return ``True`` iff *content* contains any curated slur as a whole word."""
    return _PATTERN.search(content) is not None


def slur_count() -> int:
    """Number of curated entries. Useful for diagnostics and tests."""
    return len(_SLURS)
