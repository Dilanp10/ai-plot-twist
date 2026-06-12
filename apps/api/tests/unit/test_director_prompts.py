"""Unit tests: director prompts loader + hash audit.

Module 006 / Task T-005.

Coverage:
  - load_system_prompt() returns the verbatim file contents.
  - SHA-256 constants match the on-disk files (hash audit).
  - render_user_prompt() fills in every section + each twist line.
  - StrictUndefined surfaces a missing-context bug as a runtime error.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from jinja2 import UndefinedError

from app.domain.director_prompts import (
    DIRECTOR_V1_SYSTEM_SHA256,
    DIRECTOR_V1_USER_SHA256,
    ChapterBrief,
    CurrentChapterInput,
    DirectorContext,
    SeasonInput,
    TwistInput,
    current_hashes,
    load_system_prompt,
    render_user_prompt,
)


def _ctx() -> DirectorContext:
    return DirectorContext(
        season=SeasonInput(
            bible_json={
                "tone": "drama ligero",
                "themes": ["identidad", "comunidad"],
            }
        ),
        last_chapters=[
            ChapterBrief(
                day_index=1,
                title="La Señal",
                synopsis="Valentina recibe un mensaje cifrado.",
            ),
            ChapterBrief(
                day_index=2,
                title="El Sótano",
                synopsis="Encuentra el servidor oculto.",
            ),
        ],
        current=CurrentChapterInput(
            day_index=3,
            title="La Voz",
            synopsis="Una voz responde desde el otro lado.",
            manifest_json={"cliffhanger": "¿Quién es la voz?"},
        ),
        batch=[
            TwistInput(
                public_id=UUID("11111111-1111-1111-1111-111111111111"),
                content="La voz es su yo del futuro.",
            ),
            TwistInput(
                public_id=UUID("22222222-2222-2222-2222-222222222222"),
                content="Es una IA que la observa hace años.",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def test_load_system_prompt_contains_director_role() -> None:
    text = load_system_prompt()
    assert 'Sos el "Director"' in text
    assert "CRITERIOS:" in text
    assert "REGLAS DE ORO:" in text


def test_load_system_prompt_is_stable_across_calls() -> None:
    assert load_system_prompt() == load_system_prompt()


# ---------------------------------------------------------------------------
# Hash audit
# ---------------------------------------------------------------------------


def test_prompt_hashes_match() -> None:
    """If this fails, bump the SHA-256 constants in director_prompts.py.

    The point of the audit is to force a code change whenever a prompt
    is edited, so reviewers see the new hash and consciously accept it.
    """
    system_sha, user_sha = current_hashes()
    assert (
        system_sha == DIRECTOR_V1_SYSTEM_SHA256
    ), f"system prompt drifted; update constant to {system_sha!r}"
    assert (
        user_sha == DIRECTOR_V1_USER_SHA256
    ), f"user template drifted; update constant to {user_sha!r}"


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def test_render_user_prompt_includes_each_section() -> None:
    rendered = render_user_prompt(_ctx())
    assert "BIBLE DE LA TEMPORADA" in rendered
    assert "ÚLTIMOS 3 CAPÍTULOS" in rendered
    assert "CAPÍTULO ACTUAL" in rendered
    assert "PROPUESTAS A CLASIFICAR" in rendered
    assert "Devolvé el JSON." in rendered


def test_render_user_prompt_serializes_bible_as_json() -> None:
    rendered = render_user_prompt(_ctx())
    # tojson filter produces compact JSON with sorted keys etc.
    assert '"tone": "drama ligero"' in rendered or '"tone":"drama ligero"' in rendered


def test_render_user_prompt_emits_each_chapter_line() -> None:
    rendered = render_user_prompt(_ctx())
    assert "Día 1 — La Señal: Valentina recibe un mensaje cifrado." in rendered
    assert "Día 2 — El Sótano: Encuentra el servidor oculto." in rendered


def test_render_user_prompt_shows_current_chapter_cliffhanger() -> None:
    rendered = render_user_prompt(_ctx())
    assert "Día 3 — La Voz" in rendered
    assert "Cliffhanger: ¿Quién es la voz?" in rendered


def test_render_user_prompt_lists_twists_with_uuid_brackets() -> None:
    rendered = render_user_prompt(_ctx())
    assert "[11111111-1111-1111-1111-111111111111] La voz es su yo del futuro." in rendered
    assert "[22222222-2222-2222-2222-222222222222] Es una IA que la observa hace años." in rendered


def test_render_user_prompt_strict_undefined_surfaces_missing_attr() -> None:
    """A bug where the template references an undeclared variable must error,
    not silently emit an empty string."""
    bad_ctx = DirectorContext(
        season=SeasonInput(bible_json={"tone": "x"}),
        last_chapters=[],
        current=CurrentChapterInput(
            day_index=1,
            title="t",
            synopsis="s",
            # Missing 'cliffhanger' key in manifest_json — the template
            # accesses it, so StrictUndefined raises.
            manifest_json={},
        ),
        batch=[],
    )
    with pytest.raises(UndefinedError):
        render_user_prompt(bad_ctx)
