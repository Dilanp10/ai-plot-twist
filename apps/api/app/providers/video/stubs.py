"""Paid T2V provider stubs — NotImplementedError placeholders.

Module 012 / Task T-008.

These classes satisfy the :class:`~app.providers.video.base.VideoProvider`
ABC so they can be placed in a chain without import errors, while raising
:exc:`NotImplementedError` on every method call. The router propagates
``NotImplementedError`` immediately as a misconfiguration signal (no
failover) — this makes accidental inclusion in a live chain loud and
obvious.

The stubs are intentionally in a single file to keep the module clean.
Promote each to its own file once a real implementation is added.
"""

from __future__ import annotations

from typing import Any

from app.providers.video.base import VideoProvider, VideoRequest, VideoResult

_MSG = (
    "{cls} is a paid stub — implement or remove it from the chain. "
    "See specs/012-video-providers/plan.md § paid stubs."
)


class KlingProvider(VideoProvider):
    """Stub for Kling (paid) — raises :exc:`NotImplementedError`."""

    name = "kling"

    def __init__(self, **_kwargs: Any) -> None:
        pass

    async def health(self) -> bool:
        raise NotImplementedError(_MSG.format(cls="KlingProvider"))

    async def generate(self, req: VideoRequest) -> VideoResult:
        raise NotImplementedError(_MSG.format(cls="KlingProvider"))

    @property
    def capabilities(self) -> dict[str, Any]:
        raise NotImplementedError(_MSG.format(cls="KlingProvider"))


class RunwayProvider(VideoProvider):
    """Stub for Runway Gen-3 (paid) — raises :exc:`NotImplementedError`."""

    name = "runway"

    def __init__(self, **_kwargs: Any) -> None:
        pass

    async def health(self) -> bool:
        raise NotImplementedError(_MSG.format(cls="RunwayProvider"))

    async def generate(self, req: VideoRequest) -> VideoResult:
        raise NotImplementedError(_MSG.format(cls="RunwayProvider"))

    @property
    def capabilities(self) -> dict[str, Any]:
        raise NotImplementedError(_MSG.format(cls="RunwayProvider"))


class LumaProvider(VideoProvider):
    """Stub for Luma Dream Machine (paid) — raises :exc:`NotImplementedError`."""

    name = "luma"

    def __init__(self, **_kwargs: Any) -> None:
        pass

    async def health(self) -> bool:
        raise NotImplementedError(_MSG.format(cls="LumaProvider"))

    async def generate(self, req: VideoRequest) -> VideoResult:
        raise NotImplementedError(_MSG.format(cls="LumaProvider"))

    @property
    def capabilities(self) -> dict[str, Any]:
        raise NotImplementedError(_MSG.format(cls="LumaProvider"))
