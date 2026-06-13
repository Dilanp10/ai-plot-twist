"""Director verdict — Pydantic models for the LLM's structured output.

Module 006 / Task T-006.

These two models mirror ``specs/006-directors-filter/contracts/
director-response.schema.json``. They are the ``response_schema``
passed to :meth:`LLMProvider.chat_json`, so the contract, the runtime
validation, and the wire format are all one source-of-truth chain.

The decision strings map 1:1 to the ``twists.status`` column after
persistence (T-010), modulo ``deleted_by_user`` which never comes from
the filter.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Decision enum
# ---------------------------------------------------------------------------

Decision = Literal[
    "approved",
    "rejected_offensive",
    "rejected_incoherent",
    "rejected_spam",
]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class DirectorVerdict(BaseModel):
    """One verdict for a single twist.

    ``twist_id`` MUST match a ``public_id`` the model was given in the
    user prompt; unknown ids are ignored by the filter (research R-004).
    ``reason`` is a short Spanish justification, capped at 80 characters
    by both the schema and the LLM prompt.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    twist_id: UUID
    decision: Decision
    reason: str = Field(..., min_length=1, max_length=80)


class DirectorBatchResponse(BaseModel):
    """One LLM call returns a list of verdicts.

    The list MAY be shorter than the input batch — missing verdicts are
    backfilled as ``rejected_incoherent`` by the default-deny policy in
    T-010 (research R-004).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    verdicts: list[DirectorVerdict] = Field(default_factory=list)
