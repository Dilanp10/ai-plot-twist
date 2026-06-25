"""Fake I2V provider — returns placeholder MP4 bytes for tests and dev.

Delta 008.

Reads placeholder bytes from the path set in the constructor (defaults to
``assets/placeholder.mp4``).  If the file is missing, falls back to 8 bytes
of zero-filled data so unit tests don't need assets present.

Never raises I2VProviderError in normal operation; always returns a result.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .base import I2VInvalidOutput, I2VRequest, I2VResult, ImageToVideoProvider

_FAKE_DURATION_S = 10.0
_FALLBACK_BYTES = b"\x00" * 8  # minimal stub when no file available


class FakeImageToVideoProvider(ImageToVideoProvider):
    """Deterministic stub that returns bytes from a local MP4 file."""

    name = "fake_i2v"

    def __init__(self, placeholder_path: Path | str | None = None) -> None:
        if placeholder_path is None:
            # Resolve relative to the project root (apps/api/)
            placeholder_path = (
                Path(__file__).parent.parent.parent.parent / "assets" / "placeholder.mp4"
            )
        self._path = Path(placeholder_path)

    async def health(self) -> bool:
        return True

    async def generate(self, req: I2VRequest) -> I2VResult:
        if req.duration_s <= 0:
            raise I2VInvalidOutput("duration_s must be > 0")

        t0 = time.monotonic()
        bytes_ = self._path.read_bytes() if self._path.exists() else _FALLBACK_BYTES

        latency_ms = int((time.monotonic() - t0) * 1000)
        return I2VResult(
            bytes_=bytes_,
            provider=self.name,
            model="fake",
            duration_s=_FAKE_DURATION_S,
            latency_ms=latency_ms,
            cost_usd=0.0,
        )

    @property
    def capabilities(self) -> dict[str, Any]:
        return {
            "max_duration_s": 10.0,
            "supported_aspects": ["9:16", "16:9", "1:1"],
        }
