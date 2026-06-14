"""Content-addressed R2 object-key helper for rendered panels.

Module 009 / Task T-006.

Returns paths of the form::

  seasons/{season_slug}/{chapter_public_id}/{panel_idx}-{content_hash}.{ext}

Content-addressing lets us cache panels safely: re-runs of the same
prompt against the same provider produce the same bytes → the same
hash → the same path → CDN caches stay warm. Different bytes (failover
to HuggingFace, a re-render) get a different hash → a fresh URL → no
stale cache.

Pure, deterministic, no I/O. Tests assert hash stability + extension
mapping.
"""

from __future__ import annotations

import hashlib

from app.providers.image.base import ImageResult

_MIME_TO_EXT: dict[str, str] = {
    "image/webp": "webp",
    "image/png": "png",
    "image/jpeg": "jpg",
}

# Length of the hex-truncated content hash. 16 hex chars = 64 bits of
# collision resistance: even at 10⁶ rendered panels, the birthday
# probability of a collision is ~3·10⁻⁸ — fine for content addressing.
_HASH_LEN = 16


class UnsupportedMimeType(ValueError):
    """Raised when ``ImageResult.mime_type`` is not one of the three allowed values.

    The :class:`ImageResult` Literal already constrains this at the type
    level; this exception is a defensive runtime guard for future
    drift (e.g. a provider mis-tags a result).
    """


def compute_r2_path(
    season_slug: str,
    chapter_public_id: str,
    panel_idx: int,
    image_result: ImageResult,
) -> str:
    """Compute the R2 key for one rendered panel.

    Parameters
    ----------
    season_slug:
        URL-safe season identifier (validated upstream by module 003).
    chapter_public_id:
        Chapter's public UUID as a string.
    panel_idx:
        0-based index of the panel within the chapter.
    image_result:
        The successful :class:`ImageResult` from the router.

    Raises
    ------
    UnsupportedMimeType
        ``image_result.mime_type`` is not webp/png/jpeg.
    """
    ext = _MIME_TO_EXT.get(image_result.mime_type)
    if ext is None:
        raise UnsupportedMimeType(
            f"unsupported mime_type: {image_result.mime_type!r}"
        )
    digest = hashlib.sha256(image_result.bytes_).hexdigest()[:_HASH_LEN]
    return (
        f"seasons/{season_slug}/{chapter_public_id}/"
        f"{panel_idx}-{digest}.{ext}"
    )
