"""Image (text-to-image) provider abstractions.

Module 009 owns this package; module 008 consumes it via
:func:`chain_for_env`. Business modules (``app/api``, ``app/domain``,
``app/scripts``) MUST import from this package root — never from
individual provider sub-modules — so the import-graph guard test
(T-008) can keep us honest about which files own the HTTP details.

Public API:

* :class:`ImageProvider` — narrow ABC every provider implements.
* :class:`ImageRequest` / :class:`ImageResult` — frozen dataclasses for
  the call shape.
* :class:`ImageProviderError` and its three typed subclasses — drive
  router fallback semantics (R-002).
* :class:`ImageProviderRouter` — fallback orchestrator across a chain.
* :func:`chain_for_env` — build the right chain for ``dev`` / ``mvp`` /
  ``v02`` so consumers do not hard-code provider identities.
"""

from __future__ import annotations

from typing import Literal

from app.providers.image.base import (
    ImageProvider,
    ImageProviderError,
    ImageProviderInvalidOutput,
    ImageProviderRateLimited,
    ImageProviderUnavailable,
    ImageRequest,
    ImageResult,
)
from app.providers.image.fake import FakeImageProvider
from app.providers.image.huggingface import HuggingFaceProvider
from app.providers.image.pollinations import PollinationsProvider
from app.providers.image.router import ImageProviderRouter

__all__ = [
    "ImageProvider",
    "ImageProviderError",
    "ImageProviderInvalidOutput",
    "ImageProviderRateLimited",
    "ImageProviderRouter",
    "ImageProviderUnavailable",
    "ImageRequest",
    "ImageResult",
    "chain_for_env",
]


_Env = Literal["mvp", "dev", "v02"]


def chain_for_env(
    env: _Env,
    *,
    huggingface_token: str | None = None,
) -> list[ImageProvider]:
    """Build the provider chain for the given environment.

    ``mvp``
        ``[PollinationsProvider, HuggingFaceProvider]`` — production
        default. Requires ``huggingface_token`` to be non-empty; the
        HF provider's constructor would otherwise raise.

    ``dev``
        ``[FakeImageProvider]`` — local development + CI never hits an
        external T2I service.

    ``v02``
        Raises :class:`NotImplementedError` — :class:`LocalComfyProvider`
        is reserved (see ``docs/adr/0003-image-provider-v02.md``).
    """
    if env == "dev":
        return [FakeImageProvider()]
    if env == "mvp":
        if not huggingface_token:
            raise ValueError(
                "chain_for_env('mvp') requires a non-empty huggingface_token"
            )
        return [
            PollinationsProvider(),
            HuggingFaceProvider(token=huggingface_token),
        ]
    if env == "v02":
        raise NotImplementedError(
            "chain_for_env('v02') is reserved for LocalComfyProvider; "
            "see docs/adr/0003-image-provider-v02.md."
        )
    raise ValueError(f"unknown env: {env!r}")
