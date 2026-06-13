"""Director prompts — file-based loader + SHA-256 hash audit.

Module 006 / Task T-005.

The director's filter prompt lives in two files under ``app/prompts/``:
  - ``director_v1.system.txt`` — system prompt (stable rules of the game)
  - ``director_v1.user.j2``    — Jinja2 template for the batched call

Both are loaded once on import; tests pin to their current SHA-256 so
any prompt edit forces a constant update (research R-003 hash audit).
Bumping to ``director_v2`` is a git rename + new constants — never a
hot-edit of v1.

The :func:`render_user_prompt` function fills the template with a
:class:`DirectorContext` value object. The context is a flat dataclass
so callers (T-010 the service) can build it without knowing Jinja.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from app.domain.director_context import (
    ChapterBrief,
    CurrentChapterInput,
    DirectorContext,
    SeasonInput,
    TwistInput,
)

__all__ = [
    "DIRECTOR_V1_SYSTEM_SHA256",
    "DIRECTOR_V1_USER_SHA256",
    "ChapterBrief",
    "CurrentChapterInput",
    "DirectorContext",
    "SeasonInput",
    "TwistInput",
    "current_hashes",
    "load_system_prompt",
    "render_user_prompt",
]

# ---------------------------------------------------------------------------
# Paths + module-level cached file contents
# ---------------------------------------------------------------------------

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
_SYSTEM_PATH = _PROMPTS_DIR / "director_v1.system.txt"
_USER_TEMPLATE_PATH = _PROMPTS_DIR / "director_v1.user.j2"

_SYSTEM_TEXT = _SYSTEM_PATH.read_text(encoding="utf-8")
_USER_TEMPLATE_TEXT = _USER_TEMPLATE_PATH.read_text(encoding="utf-8")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Hash constants — bumped MANUALLY whenever the prompt files change.
# `tests/unit/test_director_prompts.py::test_prompt_hashes_match` enforces it.
# ---------------------------------------------------------------------------

DIRECTOR_V1_SYSTEM_SHA256: str = (
    "9db6dc40aea9c9ca2e74738de4ab7d296124baf78b0c33bcb051fbba2ca87775"
)
DIRECTOR_V1_USER_SHA256: str = (
    "391aa3e55aeb11fed899470569cab5f0b653a785a969c4f1bdab48f8327a73d1"
)


# ---------------------------------------------------------------------------
# Jinja environment (StrictUndefined surfaces typos as runtime errors)
# ---------------------------------------------------------------------------


_JINJA_ENV = Environment(
    undefined=StrictUndefined,
    autoescape=False,  # this is for an LLM, not HTML
    keep_trailing_newline=True,
)
_USER_TEMPLATE = _JINJA_ENV.from_string(_USER_TEMPLATE_TEXT)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_system_prompt() -> str:
    """Return the v1 director system prompt verbatim."""
    return _SYSTEM_TEXT


def render_user_prompt(ctx: DirectorContext) -> str:
    """Render the v1 user prompt with the given context.

    Each ``TwistInput.public_id`` is rendered as its canonical UUID
    string form so the LLM can echo it back as ``twist_id`` in the
    response — making the round-trip lossless.
    """
    return _USER_TEMPLATE.render(
        season=ctx.season,
        last_chapters=ctx.last_chapters,
        current=ctx.current,
        batch=[
            {"public_id": str(t.public_id), "content": t.content}
            for t in ctx.batch
        ],
    )


def current_hashes() -> tuple[str, str]:
    """Return the actual hashes of the on-disk prompt files.

    Helper for the audit test — keeps the constants honest.
    """
    return _sha256(_SYSTEM_TEXT), _sha256(_USER_TEMPLATE_TEXT)
