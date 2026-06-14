"""LocalComfyProvider — RESERVED for v0.2.

Module 009 / Task T-007.

Concrete shape per SDD §4.5.2: HTTP POST to a Cloudflare Tunnel
exposing a local ComfyUI instance. Builds a ComfyUI workflow JSON,
POSTs to ``/prompt``, polls ``/history/{prompt_id}`` until the image
is ready, then fetches the result.

Implementing this in MVP would block on a stable home-GPU + tunnel
setup the PO does not yet have. Instantiation raises immediately so
nobody accidentally wires it into a chain.

ADR-0003 documents the deferral.
"""

from __future__ import annotations

from typing import Any, NoReturn

from app.providers.image.base import (
    ImageProvider,
    ImageRequest,
    ImageResult,
)


class LocalComfyProvider(ImageProvider):
    """Stub. Raises :class:`NotImplementedError` on construction.

    Reserved for v0.2. See ``docs/adr/0003-image-provider-v02.md``.
    """

    name = "local_comfy"

    def __init__(self, *_args: Any, **_kwargs: Any) -> NoReturn:
        raise NotImplementedError(
            "LocalComfyProvider is reserved for v0.2. "
            "See docs/adr/0003-image-provider-v02.md for the timeline."
        )

    async def health(self) -> bool:
        raise NotImplementedError

    async def generate(self, req: ImageRequest) -> ImageResult:
        raise NotImplementedError

    @property
    def capabilities(self) -> dict[str, Any]:
        raise NotImplementedError
