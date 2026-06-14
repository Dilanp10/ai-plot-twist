"""Manifest builder — pure functions that assemble ``chapters.manifest_json``.

Module 008 / Task T-005.

Produces the v1.0 shape defined in ``contracts/manifest-shape.md``:

    {
        "schema_version": "1.0",
        "panels":              [...],
        "cliffhanger":         "...",
        "next_cliffhanger_seed": "...",
        "winner_metadata":     {...},
        "generation_metadata": {...}
    }

All functions are pure: they take already-computed values and return a
plain ``dict[str, Any]`` ready for ``chapters.manifest_json`` (JSONB).
No DB calls, no I/O, no side-effects.

The coordinator (T-010) calls :func:`build_manifest` exactly once after
all panels are settled. The result is passed verbatim to the ``chapters``
INSERT.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.scriptwriter_response import ScriptwriterResponse
from app.domain.winner_selector import WinnerPick

__all__ = [
    "SCHEMA_VERSION",
    "GenerationMetadata",
    "ManifestPanel",
    "build_manifest",
    "winner_metadata_dict",
]

SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Intermediate dataclasses — produced by the panel pipeline (T-009)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManifestPanel:
    """Settled state of one panel after image render + TTS + R2 upload.

    ``image_url`` is always non-None: on failure it is the static
    ``PLACEHOLDER_IMAGE_URL`` (FR-010). ``tts_url`` is None when TTS is
    disabled or fails (best-effort, FR-005).
    """

    idx: int
    image_url: str
    image_blurhash: str | None
    tts_url: str | None
    narration: str
    mood: str
    provider_used: str


# ---------------------------------------------------------------------------
# GenerationMetadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerationMetadata:
    """Ops-only record attached to every manifest (never exposed by module 004).

    ``degraded_reasons`` uses stable codes:
      - ``panel_N_render_failed`` for image-render failures (N is the panel idx).
      - ``tts_N_failed`` for TTS failures that were logged (informational only).
      - ``deadline_exceeded`` when the coordinator hit the hard deadline.
      - ``scriptwriter_retry`` when the scriptwriter needed its one retry.
    """

    scriptwriter_model: str
    scriptwriter_provider: str
    panel_provider_breakdown: dict[str, int]
    tts_provider: str | None
    started_at: str
    finished_at: str
    duration_ms: int
    degraded: bool
    degraded_reasons: list[str]


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------


def winner_metadata_dict(pick: WinnerPick) -> dict[str, Any]:
    """Convert a :class:`WinnerPick` to the ``winner_metadata`` dict shape.

    ``pick.winner_public_id`` (the twist's *public* UUID) becomes
    ``winner_twist_id`` in the manifest, matching the contract. The
    internal integer ``winner_twist_id`` from the SQL row is NOT stored.
    """
    return {
        "winner_twist_id": (
            str(pick.winner_public_id) if pick.winner_public_id is not None else None
        ),
        "winner_author_display_name": pick.winner_user_display_name,
        "vote_count": pick.vote_count,
        "tiebreak": pick.tiebreak,
        "runner_up_twist_id": (
            str(pick.runner_up_twist_id)
            if pick.runner_up_twist_id is not None
            else None
        ),
    }


def build_manifest(
    *,
    script: ScriptwriterResponse,
    panels: list[ManifestPanel],
    winner: WinnerPick,
    gen_meta: GenerationMetadata,
) -> dict[str, Any]:
    """Assemble the full ``manifest_json`` dict (schema_version 1.0).

    Parameters
    ----------
    script:
        Parsed scriptwriter LLM output.
    panels:
        Settled panel list from the panel pipeline, one entry per script panel.
        Must be sorted by ``idx`` and contain exactly ``len(script.panels)``
        entries.
    winner:
        Winner pick from :func:`~app.domain.winner_selector.pick_winner`.
        All fields are None / 0 in auto-continue mode.
    gen_meta:
        Coordinator-assembled generation metadata.

    Returns
    -------
    dict[str, Any]
        JSON-serializable dict ready for ``chapters.manifest_json``.
    """
    panel_dicts: list[dict[str, Any]] = [
        {
            "idx": p.idx,
            "image_url": p.image_url,
            "image_blurhash": p.image_blurhash,
            "tts_url": p.tts_url,
            "narration": p.narration,
            "mood": p.mood,
        }
        for p in panels
    ]

    gen_meta_dict: dict[str, Any] = {
        "scriptwriter_model": gen_meta.scriptwriter_model,
        "scriptwriter_provider": gen_meta.scriptwriter_provider,
        "panel_provider_breakdown": dict(gen_meta.panel_provider_breakdown),
        "tts_provider": gen_meta.tts_provider,
        "started_at": gen_meta.started_at,
        "finished_at": gen_meta.finished_at,
        "duration_ms": gen_meta.duration_ms,
        "degraded": gen_meta.degraded,
        "degraded_reasons": list(gen_meta.degraded_reasons),
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "panels": panel_dicts,
        "cliffhanger": script.cliffhanger,
        "next_cliffhanger_seed": script.next_cliffhanger_seed,
        "winner_metadata": winner_metadata_dict(winner),
        "generation_metadata": gen_meta_dict,
    }
