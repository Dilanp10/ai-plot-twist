"""Scriptwriter prompts — file-based loader + SHA-256 hash audit.

Module 008 / Task T-004.

The scriptwriter pipeline uses three prompt files under ``app/prompts/``:
  - ``scriptwriter_v1.system.txt``       — system prompt (winner mode)
  - ``scriptwriter_v1_auto.system.txt``  — system prompt (auto-continue mode)
  - ``scriptwriter_v1.user.j2``          — Jinja2 user template (both modes)

All three are loaded once on import. Hash constants mirror the module 006
pattern (R-003): any prompt edit MUST be accompanied by a constant bump so
reviewers see the change and consciously accept it. The test
``test_scriptwriter_prompts.py::test_prompt_hashes_match`` enforces this.

Bumping to ``scriptwriter_v2`` is a git rename + new constants.
Never hot-edit v1 in production.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined

__all__ = [
    "SCRIPTWRITER_V1_AUTO_SHA256",
    "SCRIPTWRITER_V1_SYSTEM_SHA256",
    "SCRIPTWRITER_V1_USER_SHA256",
    "ChapterBrief",
    "ScriptContext",
    "SeasonBrief",
    "current_hashes",
    "load_auto_system_prompt",
    "load_system_prompt",
    "render_user_prompt",
]

# ---------------------------------------------------------------------------
# Paths + module-level cached file contents
# ---------------------------------------------------------------------------

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
_SYSTEM_PATH = _PROMPTS_DIR / "scriptwriter_v1.system.txt"
_AUTO_PATH = _PROMPTS_DIR / "scriptwriter_v1_auto.system.txt"
_USER_TEMPLATE_PATH = _PROMPTS_DIR / "scriptwriter_v1.user.j2"

_SYSTEM_TEXT = _SYSTEM_PATH.read_text(encoding="utf-8")
_AUTO_TEXT = _AUTO_PATH.read_text(encoding="utf-8")
_USER_TEMPLATE_TEXT = _USER_TEMPLATE_PATH.read_text(encoding="utf-8")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Hash constants — bumped MANUALLY whenever the prompt files change.
# ``test_scriptwriter_prompts.py::test_prompt_hashes_match`` enforces it.
# ---------------------------------------------------------------------------

SCRIPTWRITER_V1_SYSTEM_SHA256: str = (
    "2a24c6dd54950d5d54dade28311be06911106c5b0d2eab605010a15e52e8be0d"
)
SCRIPTWRITER_V1_AUTO_SHA256: str = (
    "f519039227d183c0db799a246c97465cda2e417c3c0dc75b75cfc2f8a8384205"
)
SCRIPTWRITER_V1_USER_SHA256: str = (
    "2493115e2cc0bfb04f0755c6e0e7c070865e12a9c7e0dbd0dfe13a06b350113b"
)

# ---------------------------------------------------------------------------
# Jinja environment
# ---------------------------------------------------------------------------

_JINJA_ENV = Environment(
    undefined=StrictUndefined,
    autoescape=False,
    keep_trailing_newline=True,
)
_USER_TEMPLATE = _JINJA_ENV.from_string(_USER_TEMPLATE_TEXT)

# ---------------------------------------------------------------------------
# Context dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeasonBrief:
    title: str
    bible_json: dict[str, Any]


@dataclass(frozen=True)
class ChapterBrief:
    day_index: int
    title: str
    synopsis: str
    cliffhanger: str


@dataclass(frozen=True)
class ScriptContext:
    """All inputs the scriptwriter needs to draft a chapter.

    ``winner_content`` is ``None`` in auto-continue mode (no approved
    twists). The caller selects the correct system prompt via
    :func:`load_system_prompt` vs :func:`load_auto_system_prompt`.
    """

    season: SeasonBrief
    recent_chapters: list[ChapterBrief]
    current_chapter: ChapterBrief
    next_day_index: int
    winner_content: str | None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_system_prompt() -> str:
    """Return the v1 scriptwriter system prompt (winner mode)."""
    return _SYSTEM_TEXT


def load_auto_system_prompt() -> str:
    """Return the v1 scriptwriter system prompt (auto-continue mode)."""
    return _AUTO_TEXT


def render_user_prompt(ctx: ScriptContext) -> str:
    """Render the v1 user template with the given script context.

    Works for both winner and auto-continue modes: the template uses
    ``{% if winner_content %}`` to conditionally include the winning
    twist section.
    """
    return _USER_TEMPLATE.render(
        season=ctx.season,
        recent_chapters=ctx.recent_chapters,
        current_chapter=ctx.current_chapter,
        next_day_index=ctx.next_day_index,
        winner_content=ctx.winner_content,
    )


def current_hashes() -> tuple[str, str, str]:
    """Return the actual SHA-256 hashes of the on-disk prompt files.

    Returns ``(system_sha, auto_sha, user_sha)``.
    Helper for the audit test — keeps the constants honest.
    """
    return _sha256(_SYSTEM_TEXT), _sha256(_AUTO_TEXT), _sha256(_USER_TEMPLATE_TEXT)
