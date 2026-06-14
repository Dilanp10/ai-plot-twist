"""FakeImageProvider — always returns a 1x1 transparent PNG.

Module 009 / Task T-002.

Used by:
  - the ``dev`` chain (``chain_for_env("dev")``) so local dev never hits
    external T2I APIs.
  - module 008 integration tests that need a deterministic result without
    network I/O.

The returned PNG is the canonical 67-byte transparent 1x1 image — small
enough to dump into R2 in tests, valid enough to satisfy any "is this a
PNG?" sniff downstream.
"""

from __future__ import annotations

from typing import Any

from app.providers.image.base import (
    ImageProvider,
    ImageRequest,
    ImageResult,
)

# A canonical 1x1 transparent PNG, minimal IHDR + IDAT + IEND chunks.
PNG_1x1: bytes = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDAT"
    b"x\x9cc\xfc\xff\xff?\x00\x05\xfe\x02\xfe"
    b"\xa3o\xff\x1d"
    b"\x00\x00\x00\x00IEND"
    b"\xaeB`\x82"
)


class FakeImageProvider(ImageProvider):
    """Returns the same 1x1 PNG for every prompt; no I/O."""

    name = "fake"

    async def health(self) -> bool:
        """Always reachable — there is no remote service to fail."""
        return True

    async def generate(self, req: ImageRequest) -> ImageResult:
        """Return the canonical 1x1 PNG.

        ``latency_ms`` is reported as 0; the request is consumed only to
        let the call signature match the ABC. Useful for end-to-end tests
        where the pipeline downstream cares about ``provider``/``model``
        more than the actual pixels.
        """
        del req  # signature parity with real providers
        return ImageResult(
            bytes_=PNG_1x1,
            mime_type="image/png",
            provider=self.name,
            model="fake:1x1-png",
            latency_ms=0,
            cost_usd=0.0,
        )

    @property
    def capabilities(self) -> dict[str, Any]:
        return {
            "max_resolution": (1, 1),
            "supports_seed": False,
            "supported_models": ["fake:1x1-png"],
        }
