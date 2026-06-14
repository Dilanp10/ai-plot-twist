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
"""

from app.providers.image.base import (
    ImageProvider,
    ImageProviderError,
    ImageProviderInvalidOutput,
    ImageProviderRateLimited,
    ImageProviderUnavailable,
    ImageRequest,
    ImageResult,
)

__all__ = [
    "ImageProvider",
    "ImageProviderError",
    "ImageProviderInvalidOutput",
    "ImageProviderRateLimited",
    "ImageProviderUnavailable",
    "ImageRequest",
    "ImageResult",
]
