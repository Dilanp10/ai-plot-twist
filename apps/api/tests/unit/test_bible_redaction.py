"""Unit tests: bible_redaction allowlist filter.

Module 004 / Task T-001.

Pure-function tests: no DB, no network, no time dependency.
"""

from __future__ import annotations

from typing import Any

from app.domain.bible_redaction import PUBLIC_BIBLE_KEYS, redact

# ---------------------------------------------------------------------------
# Fixtures (literal dicts — readable inline)
# ---------------------------------------------------------------------------


def _full_bible() -> dict[str, Any]:
    """A bible containing every allowlisted key plus extras."""
    return {
        "setting": "Buenos Aires, 2027",
        "tone": "drama con sci-fi ligera",
        "characters": [
            {"name": "Valentina", "role": "protagonist"},
            {"name": "El Sistema", "role": "antagonist"},
        ],
        "rules": ["no time travel", "AI is ubiquitous"],
        # NOT allowlisted — must be excluded:
        "secrets": "final episode reveals X",
        "plot_twists_planned": ["betrayal in ep 5", "death in ep 9"],
        "internal_notes": "ask PO about ending",
    }


# ---------------------------------------------------------------------------
# Allowlist behavior
# ---------------------------------------------------------------------------


def test_keeps_only_allowlisted_keys() -> None:
    result = redact(_full_bible())
    assert set(result.keys()) == PUBLIC_BIBLE_KEYS


def test_excludes_unknown_keys() -> None:
    result = redact(_full_bible())
    for forbidden in ("secrets", "plot_twists_planned", "internal_notes"):
        assert forbidden not in result


def test_empty_dict_returns_empty() -> None:
    assert redact({}) == {}


def test_only_unknown_keys_returns_empty() -> None:
    bible = {"secrets": "X", "plot_twists_planned": ["Y"]}
    assert redact(bible) == {}


def test_partial_overlap_keeps_only_present_allowlisted() -> None:
    """Mirrors docs/seed/example-cap0.yaml — only ``tone`` overlaps."""
    bible = {
        "logline": "una adolescente descubre IA",
        "themes": ["identidad", "comunidad"],
        "tone": "drama con sci-fi ligera",
        "world_notes": "Buenos Aires 2027",
    }
    assert redact(bible) == {"tone": "drama con sci-fi ligera"}


# ---------------------------------------------------------------------------
# Value preservation
# ---------------------------------------------------------------------------


def test_nested_values_preserved_verbatim() -> None:
    bible = _full_bible()
    result = redact(bible)
    assert result["characters"] == bible["characters"]
    assert result["rules"] == bible["rules"]
    assert result["setting"] == bible["setting"]
    assert result["tone"] == bible["tone"]


def test_does_not_mutate_input() -> None:
    bible = _full_bible()
    snapshot = {k: v for k, v in bible.items()}
    redact(bible)
    assert bible == snapshot


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


def test_result_keys_are_subset_of_allowlist() -> None:
    """Property: redact never adds a key, never returns a non-allowlisted one."""
    for bible in (
        {},
        _full_bible(),
        {"setting": 1},
        {"unknown": "x"},
        {"setting": 1, "unknown": "x"},
    ):
        result = redact(bible)
        assert set(result.keys()) <= PUBLIC_BIBLE_KEYS
        assert set(result.keys()) <= set(bible.keys())
