"""Content-addressed R2 key helper for generated video clips.

Module 012 / Task T-006.

Returns paths of the form::

  seasons/{season_slug}/{chapter_public_id}/clips/{clip_idx}-{sha256[:8]}.mp4

Content-addressing: re-runs of the same prompt against the same provider
produce the same bytes → the same hash → the same path → CDN caches stay
warm. Different bytes (failover to Pollinations, a re-render) get a
different hash → fresh URL → no stale cache.

Pure, deterministic, no I/O.
"""

from __future__ import annotations

import hashlib

from app.providers.video.base import VideoResult

_HASH_LEN = 8  # 8 hex chars = 32 bits; sufficient for per-chapter addressing


def compute_r2_clip_path(
    season_slug: str,
    chapter_public_id: str,
    clip_idx: int,
    result: VideoResult,
) -> str:
    """Compute the R2 object key for one generated video clip.

    Parameters
    ----------
    season_slug:
        URL-safe season identifier (validated upstream by module 003).
    chapter_public_id:
        Chapter's public UUID as a string.
    clip_idx:
        0-based index of the clip within the chapter (max ~5 clips in MVP).
    result:
        The successful :class:`VideoResult` from the router.

    Returns
    -------
    str
        R2 key: ``seasons/{slug}/{uuid}/clips/{idx}-{hash8}.mp4``
    """
    digest = hashlib.sha256(result.bytes_).hexdigest()[:_HASH_LEN]
    return (
        f"seasons/{season_slug}/{chapter_public_id}/clips/{clip_idx}-{digest}.mp4"
    )
