"""FakeVideoProvider — injectable fake for testing + MINIMAL_MP4 constant.

Module 012 / Task T-002.

Used by:
  - the ``dev`` chain (``chain_for_env("dev")``) so local dev never hits
    external T2V APIs.
  - module 008 / 012 unit + integration tests that need deterministic clip
    bytes without network I/O or ffmpeg.

Two operating modes:

``responses=None`` (default)
    Infinite mode: every ``generate()`` call returns a fresh
    :class:`~app.providers.video.base.VideoResult` backed by
    :data:`MINIMAL_MP4`. The provider never exhausts.

``responses=[…]``
    Injectable mode: ``generate()`` pops items from the list in order.
    Each item is one of:

    * :class:`~app.providers.video.base.VideoResult` — returned as-is.
    * An :class:`Exception` instance — raised directly.
    * An :class:`Exception` subclass (type, not instance) — instantiated
      and raised.

    When the list is exhausted,
    :class:`~app.providers.video.base.VideoProviderUnavailable` is raised
    with the message ``"FakeVideoProvider exhausted"``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.providers.video.base import (
    VideoProvider,
    VideoProviderUnavailable,
    VideoRequest,
    VideoResult,
)

# ---------------------------------------------------------------------------
# MINIMAL_MP4
# ---------------------------------------------------------------------------
# Hand-crafted MP4 file (ftyp + moov/mvhd) that satisfies the following:
#   - ``b"ftyp"`` and ``b"moov"`` and ``b"mvhd"`` are present.
#   - mutagen.mp4.MP4(BytesIO(MINIMAL_MP4)).info.length == 5.0 s.
#   - Total size: 136 bytes.
#
# Structure:
#   ftyp (20 bytes):  major brand "mp42", minor version 0, compat "mp42"
#   moov (116 bytes):
#     mvhd (108 bytes):  version 0, timescale 1000, duration 5000 (= 5.0 s)
#
# The mvhd identity matrix and pre_defined fields are zeroed / standard.
# The moov box intentionally omits trak/mdia — mutagen tolerates this.
MINIMAL_MP4: bytes = (
    # ── ftyp box: 20 bytes ──────────────────────────────────────────────────
    b"\x00\x00\x00\x14"      # size = 20
    b"ftyp"
    b"mp42"                  # major brand
    b"\x00\x00\x00\x00"      # minor version
    b"mp42"                  # compatible brand
    # ── moov box: 116 bytes ─────────────────────────────────────────────────
    b"\x00\x00\x00\x74"      # size = 116
    b"moov"
    # ── mvhd box: 108 bytes (FullBox version 0) ─────────────────────────────
    b"\x00\x00\x00\x6c"      # size = 108
    b"mvhd"
    b"\x00"                  # version = 0
    b"\x00\x00\x00"          # flags = 0
    b"\x00\x00\x00\x00"      # creation_time = 0
    b"\x00\x00\x00\x00"      # modification_time = 0
    b"\x00\x00\x03\xe8"      # timescale = 1000 (ms)
    b"\x00\x00\x13\x88"      # duration = 5000 → 5.0 s at timescale 1000
    b"\x00\x01\x00\x00"      # rate = 1.0 (16.16 fixed-point)
    b"\x01\x00"              # volume = 1.0 (8.8 fixed-point)
    b"\x00\x00"              # reserved (2 bytes)
    b"\x00\x00\x00\x00"      # reserved (4 bytes)
    b"\x00\x00\x00\x00"      # reserved (4 bytes)
    # identity matrix (9 x int32 = 36 bytes)
    b"\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x40\x00\x00\x00"
    # pre_defined (6 x uint32 = 24 bytes)
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    # next_track_ID
    b"\x00\x00\x00\x01"
)

# Sanity check at import time — catches accidental truncation.
assert len(MINIMAL_MP4) == 136, f"MINIMAL_MP4 size mismatch: {len(MINIMAL_MP4)}"

# ---------------------------------------------------------------------------
# FakeVideoProvider
# ---------------------------------------------------------------------------

_ResponseItem = VideoResult | type[BaseException] | BaseException


class FakeVideoProvider(VideoProvider):
    """Configurable fake for unit tests and the ``dev`` chain.

    Parameters
    ----------
    responses:
        ``None`` (default) → infinite mode, always returns a
        :class:`VideoResult` backed by :data:`MINIMAL_MP4`.

        A list → injectable mode, items are popped in order. Each item is
        a :class:`VideoResult`, an exception instance, or an exception class.
        List exhaustion raises :class:`VideoProviderUnavailable`.
    latency_ms:
        If > 0, each ``generate()`` sleeps this many milliseconds before
        returning. Useful to test deadline/concurrency logic without real I/O.
    health_returns:
        Value returned by :meth:`health`. Default ``True``.
    """

    name = "fake"

    def __init__(
        self,
        responses: list[_ResponseItem] | None = None,
        latency_ms: int = 0,
        health_returns: bool = True,
    ) -> None:
        self._responses = list(responses) if responses is not None else None
        self._latency_ms = latency_ms
        self._health_returns = health_returns

    async def health(self) -> bool:
        return self._health_returns

    async def generate(self, req: VideoRequest) -> VideoResult:
        del req  # signature parity with real providers

        if self._latency_ms > 0:
            await asyncio.sleep(self._latency_ms / 1000)

        if self._responses is None:
            return self._default_result()

        if not self._responses:
            raise VideoProviderUnavailable("FakeVideoProvider exhausted")

        item = self._responses.pop(0)

        if isinstance(item, type) and issubclass(item, BaseException):
            raise item()
        if isinstance(item, BaseException):
            raise item
        return item

    @property
    def capabilities(self) -> dict[str, Any]:
        return {
            "max_duration_s": 5.0,
            "supported_resolutions": [(512, 512)],
            "supported_fps": [24],
        }

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    @staticmethod
    def _default_result() -> VideoResult:
        return VideoResult(
            bytes_=MINIMAL_MP4,
            mime_type="video/mp4",
            provider="fake",
            model="fake",
            duration_s=5.0,
            frames_count=121,
            latency_ms=0,
            cost_usd=0.0,
        )
