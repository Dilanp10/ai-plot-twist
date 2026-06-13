"""LLM provider abstraction + concrete implementations.

Module 006 / Task T-001 (base + Fake), T-004 (Router), T-002 (Gemini).

The base ABC, typed exceptions, and the ``Fake`` provider land first
(T-001). The :class:`LLMProviderRouter` (T-004) implements the FR-004
fallback policy. :class:`GeminiProvider` (T-002) is the production
primary; :class:`GitHubModelsProvider` (T-003) is the fallback.
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
from app.providers.llm.gemini import GeminiProvider
from app.providers.llm.router import LLMProviderRouter

__all__ = [
    "FakeLLMProvider",
    "GeminiProvider",
    "LLMProvider",
    "LLMProviderError",
    "LLMProviderInvalidOutput",
    "LLMProviderRateLimited",
    "LLMProviderRouter",
    "LLMProviderUnavailable",
    "LLMResponse",
]
