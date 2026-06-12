"""LLM provider abstraction + concrete implementations.

Module 006 / Task T-001.

The base ABC, typed exceptions, and the ``Fake`` provider land first
(T-001). Real providers and the router are added in T-002..T-004.
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

__all__ = [
    "FakeLLMProvider",
    "LLMProvider",
    "LLMProviderError",
    "LLMProviderInvalidOutput",
    "LLMProviderRateLimited",
    "LLMProviderUnavailable",
    "LLMResponse",
]
