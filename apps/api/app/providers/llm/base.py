"""LLM provider base — ABC + typed exceptions + response dataclass.

Module 006 / Task T-001.

The interface is intentionally narrow: a single ``chat_json`` method
that forces structured output via a Pydantic schema. No raw-text
``chat()`` — every consumer in this app wants typed data in MVP. If
streaming becomes valuable later, add ``chat_json_stream``; do not
broaden ``chat_json``.

Typed exception hierarchy lets the :class:`LLMProviderRouter` (T-004)
implement the fallback semantics from FR-004:
  - ``RateLimited``     → skip this provider, try next
  - ``Unavailable``     → retry once with backoff, then fall through
  - ``InvalidOutput``   → skip (no retry — bad prompt or model bug)
  - any other ``Error`` → bubble to caller (operator must intervene)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class LLMProviderError(Exception):
    """Base class for all provider failures."""


class LLMProviderRateLimited(LLMProviderError):
    """Provider quota/rate exhausted (HTTP 429 or equivalent).

    Router policy: skip this provider on this attempt; try the next.
    """


class LLMProviderUnavailable(LLMProviderError):
    """Provider unreachable or returned a 5xx (HTTP 502/503/504, network).

    Router policy: retry once with backoff, then fall through.
    """


class LLMProviderInvalidOutput(LLMProviderError):
    """Provider responded but the body did not parse into the requested schema.

    Router policy: skip this provider (no retry) — either the prompt is
    broken or the model cannot follow the schema for this input.
    """


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMResponse:
    """Outcome of a successful :meth:`LLMProvider.chat_json` call.

    Attributes
    ----------
    content:
        The already-parsed Pydantic model. Type matches the
        ``response_schema`` argument passed to ``chat_json``.
    provider:
        Lower-case provider identifier (``"gemini"``, ``"github_models"``,
        ``"fake"``). Mirrors :attr:`LLMProvider.name`.
    model:
        The exact model string used (e.g. ``"gemini-2.0-flash"``).
    latency_ms:
        End-to-end wall-clock latency of the call, including SDK overhead.
    tokens_in:
        Prompt tokens consumed (provider-reported; ``0`` if unknown).
    tokens_out:
        Output tokens generated (provider-reported; ``0`` if unknown).
    """

    content: BaseModel
    provider: str
    model: str
    latency_ms: int
    tokens_in: int
    tokens_out: int


# ---------------------------------------------------------------------------
# Provider ABC
# ---------------------------------------------------------------------------


class LLMProvider(ABC):
    """Async interface implemented by every concrete LLM provider.

    Subclasses MUST set ``name`` as a class attribute and override
    :meth:`health` + :meth:`chat_json`. Constructors typically take the
    model string and credentials.
    """

    name: str

    @abstractmethod
    async def health(self) -> bool:
        """Cheap reachability check.

        Returns ``True`` when the provider's API endpoint responds. Used
        by the router (T-004) to skip down providers proactively.
        """

    @abstractmethod
    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        response_schema: type[BaseModel],
        temperature: float = 0.2,
        max_output_tokens: int = 2048,
    ) -> LLMResponse:
        """Call the model and return structured output.

        Parameters
        ----------
        system:
            System prompt (role, tone, constraints).
        user:
            User prompt (the actual task input).
        response_schema:
            Pydantic model class the response must conform to. The
            provider asks the LLM for JSON shaped to this schema and
            parses the response into an instance.
        temperature:
            Sampling temperature. Default 0.2 (mostly deterministic).
        max_output_tokens:
            Hard cap on the response length.

        Returns
        -------
        LLMResponse
            With ``content`` typed as ``response_schema`` (the caller can
            cast safely; the router preserves the typing).

        Raises
        ------
        LLMProviderRateLimited
        LLMProviderUnavailable
        LLMProviderInvalidOutput
        LLMProviderError
            For anything else (auth failure, malformed credentials, etc.).
        """
