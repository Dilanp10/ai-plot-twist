"""Public-safe redaction of the season ``bible_json`` blob.

Module 004 / Task T-001.

The season "bible" is a free-form JSON document maintained by the PO that holds
both public-facing world-building (setting, characters, rules) and private
authorial material that must never reach the client (future spoilers, planned
plot twists, internal notes).

This module exposes a **top-level allowlist filter** that converts the raw
``bible_json`` into the public-safe subset rendered by
``GET /api/v1/seasons/{slug}`` (spec FR-008).

Design choice — opt-in, not opt-out (Gate 9 — trust boundaries):
  New top-level keys added to a future bible are excluded by default. To make
  a new key public, it MUST be added explicitly to ``PUBLIC_BIBLE_KEYS`` AND
  the addition tested. This guarantees a "secrets" or "plot_twists_planned"
  key cannot leak through accidentally.

The filter operates only at the top level. Nested values inside an allowlisted
key are preserved verbatim — the contract assumes the PO keeps sensitive
material in dedicated top-level keys, not nested deep under a public one.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------

PUBLIC_BIBLE_KEYS: frozenset[str] = frozenset({"setting", "tone", "characters", "rules"})
"""Top-level keys of ``bible_json`` exposed to anonymous clients.

Adding a key here is a public-API change: bump the contract test in
``tests/contract/test_chapters_contract.py`` and the OpenAPI in
``specs/004-chapters-content/contracts/chapters.yaml``.
"""


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------


def redact(bible: dict[str, Any]) -> dict[str, Any]:
    """Return the public-safe subset of *bible*.

    Keeps only top-level keys present in :data:`PUBLIC_BIBLE_KEYS`. The input
    dict is **not** mutated; a new dict is returned. Values for allowlisted
    keys are referenced verbatim (shallow copy) — mutating the result's nested
    structures would mutate the source too, which callers must not do.

    Parameters
    ----------
    bible:
        The raw ``bible_json`` blob from ``seasons.bible_json``. May be empty
        or missing any/all allowlisted keys.

    Returns
    -------
    dict[str, Any]
        New dict with at most ``len(PUBLIC_BIBLE_KEYS)`` keys. Empty if no
        allowlisted key is present in *bible*.
    """
    return {k: v for k, v in bible.items() if k in PUBLIC_BIBLE_KEYS}
