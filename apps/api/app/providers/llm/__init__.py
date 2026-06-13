"""LLM provider abstraction + concrete implementations.

Module 006 / Task T-001 (base + Fake) and T-004 (Router).

The base ABC, typed exceptions, and the ``Fake`` provider land first
(T-001). The :class:`LLMProviderRouter` (T-004) implements the FR-004
fallback policy. Real providers Gemini/GH Models follow in T-002/T-003.
"""

from app.providers.llm.base import (
    LLMProvider,
    LLMProviderError,
    LLMProviderInvalidOutput,
    LLMProviderRateLimited,
    LLMProviderUnavailable,
    LLMResponse,
)
from app.providers.llm.fake import FakeLLMProvider
from app.providers.llm.router import LLMProviderRouter

__all__ = [
    "FakeLLMProvider",
    "LLMProvider",
    "LLMProviderError",
    "LLMProviderInvalidOutput",
    "LLMProviderRateLimited",
    "LLMProviderRouter",
    "LLMProviderUnavailable",
    "LLMResponse",
]
