"""Deterministic seed derivation for the panel-rendering pipeline.

Module 008 / Task T-002.

Produces a reproducible 32-bit unsigned integer from (chapter_id, panel_idx)
so that each panel's T2I call uses a stable seed across re-runs and reruns of
the same chapter (FR-004 / Gate 5).

Using SHA-256 ensures:
  - Uniform distribution across the uint32 range.
  - No accidental collisions from simple arithmetic combinations.
  - The same guarantees as the vote-sort seed (module 007) without coupling
    the implementations.
"""

from __future__ import annotations

import hashlib
import struct


def stable_hash(chapter_id: int, panel_idx: int) -> int:
    """Return a deterministic 32-bit unsigned integer for *(chapter_id, panel_idx)*.

    The value is in the range [0, 2**32 - 1], always non-negative, and
    identical across processes and platforms for the same inputs.
    """
    digest = hashlib.sha256(
        f"{chapter_id}:{panel_idx}".encode()
    ).digest()
    # Big-endian unsigned 32-bit integer from the first 4 bytes.
    return int(struct.unpack_from(">I", digest)[0])
