"""DirectorContext — value object passed to the director user-prompt renderer.

Module 006 / Task T-008.

This is the input the filter service (T-009) builds from DB rows and
hands to :func:`app.domain.director_prompts.render_user_prompt`. Keeping
it in its own module decouples the prompt encoding (Jinja file + hash
audit) from the data shape, so the service can construct contexts
without importing the prompts module's filesystem side-effects.

The dataclasses are intentionally flat and frozen — they cross the
service → renderer boundary and the renderer iterates over their fields
directly in the Jinja template (`StrictUndefined` surfaces typos as
runtime errors).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class SeasonInput:
    """Season slice the director sees: just the bible JSON.

    The bible is the FULL (un-redacted) version. The LLM is a trusted
    internal consumer and needs setting/tone/rules to judge coherence;
    module 004 owns redaction for the public-facing endpoints.
    """

    bible_json: dict[str, Any]


@dataclass(frozen=True)
class ChapterBrief:
    """Compact view of a past chapter for the recent-history block.

    Used for the last-3-chapters context window so the LLM can judge
    continuity / cliffhanger relevance.
    """

    day_index: int
    title: str
    synopsis: str


@dataclass(frozen=True)
class CurrentChapterInput:
    """Current chapter shape — full manifest is needed for the cliffhanger.

    ``manifest_json["cliffhanger"]`` is what the twists are supposed to
    resolve, so it goes into the prompt verbatim.
    """

    day_index: int
    title: str
    synopsis: str
    manifest_json: dict[str, Any]


@dataclass(frozen=True)
class TwistInput:
    """One twist to classify.

    ``public_id`` is the UUID the verdict must echo back as ``twist_id``
    so the round-trip is lossless (research R-004).
    """

    public_id: UUID
    content: str


@dataclass(frozen=True)
class DirectorContext:
    """All variables the user template references.

    Construct from DB rows in the filter service (T-009), then hand to
    :func:`app.domain.director_prompts.render_user_prompt`. The dataclass
    and the Jinja template are the only places the prompt input schema
    is encoded.
    """

    season: SeasonInput
    last_chapters: list[ChapterBrief]
    current: CurrentChapterInput
    batch: list[TwistInput]
