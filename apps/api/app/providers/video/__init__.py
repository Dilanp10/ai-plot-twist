"""Video (text-to-video) provider abstractions.

Module 012 owns this package; module 008 consumes it via
:func:`chain_for_env`. Business modules (``app/api``, ``app/domain``,
``app/scripts``) MUST import from this package root — never from
individual provider sub-modules — so the import-graph guard test
(T-010) can keep us honest about which files own the HTTP details.

Public API:

* :class:`VideoProvider` — narrow ABC every provider implements.
* :class:`VideoRequest` / :class:`VideoResult` — frozen dataclasses for
  the call shape.
* :class:`VideoProviderError` and its three typed subclasses — drive
  router fallback semantics.
* :class:`VideoProviderRouter` — fallback orchestrator across a chain.
* :func:`chain_for_env` — build the right chain for ``dev`` / ``mvp``
  so consumers do not hard-code provider identities.
* :func:`compute_r2_clip_path` — content-addressed R2 key for one clip.
* :data:`MINIMAL_MP4` — 136-byte valid MP4 constant (tests / dev).
"""

from __future__ import annotations

from typing import Literal

from app.providers.video.base import (
    VideoProvider,
    VideoProviderError,
    VideoProviderInvalidOutput,
    VideoProviderRateLimited,
    VideoProviderUnavailable,
    VideoRequest,
    VideoResult,
)
from app.providers.video.fake import MINIMAL_MP4, FakeVideoProvider
from app.providers.video.hf import HFVideoProvider
from app.providers.video.paths import compute_r2_clip_path
from app.providers.video.pollinations import PollinationsVideoProvider
from app.providers.video.router import VideoProviderRouter

__all__ = [
    "MINIMAL_MP4",
    "FakeVideoProvider",
    "HFVideoProvider",
    "PollinationsVideoProvider",
    "VideoProvider",
    "VideoProviderError",
    "VideoProviderInvalidOutput",
    "VideoProviderRateLimited",
    "VideoProviderRouter",
    "VideoProviderUnavailable",
    "VideoRequest",
    "VideoResult",
    "chain_for_env",
    "compute_r2_clip_path",
]

_Env = Literal["mvp", "dev"]


def chain_for_env(
    env: _Env,
    *,
    huggingface_token: str | None = None,
) -> list[VideoProvider]:
    """Build the T2V provider chain for the given environment.

    ``dev``
        ``[FakeVideoProvider()]`` — local development and CI never hit
        an external T2V API.

    ``mvp``
        ``[HFVideoProvider, PollinationsVideoProvider]`` — free-tier
        production default. Requires ``huggingface_token`` to be non-empty;
        the HF provider's constructor raises ``ValueError`` otherwise.
    """
    if env == "dev":
        return [FakeVideoProvider()]
    if env == "mvp":
        if not huggingface_token:
            raise ValueError(
                "chain_for_env('mvp') requires a non-empty huggingface_token"
            )
        return [
            HFVideoProvider(token=huggingface_token),
            PollinationsVideoProvider(),
        ]
    raise ValueError(f"unknown env: {env!r}")
