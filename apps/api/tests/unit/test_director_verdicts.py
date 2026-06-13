"""Unit tests: director verdict models + schema parity with the contract.

Module 006 / Task T-006.

Coverage:
  - Parses the contract example verbatim.
  - Rejects unknown fields (additionalProperties: false).
  - Rejects out-of-range reason length and empty reason.
  - Rejects unknown decision values.
  - Schema parity: Pydantic's JSON schema agrees with the contract on
    the load-bearing keys (required, enum, maxLength, additionalProperties).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.domain.director_verdicts import (
    DirectorBatchResponse,
    DirectorVerdict,
)

_CONTRACT_PATH = (
    Path(__file__).parent.parent.parent.parent.parent
    / "specs"
    / "006-directors-filter"
    / "contracts"
    / "director-response.schema.json"
)


def _contract() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
    return data


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_parses_the_contract_example() -> None:
    contract = _contract()
    example: dict[str, Any] = contract["examples"][0]
    parsed = DirectorBatchResponse.model_validate(example)
    assert len(parsed.verdicts) == 3
    assert parsed.verdicts[0].decision == "approved"
    assert parsed.verdicts[1].decision == "rejected_incoherent"
    assert parsed.verdicts[2].decision == "rejected_offensive"


def test_empty_verdicts_list_is_allowed() -> None:
    parsed = DirectorBatchResponse.model_validate({"verdicts": []})
    assert parsed.verdicts == []


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------


def test_rejects_unknown_top_level_field() -> None:
    with pytest.raises(ValidationError):
        DirectorBatchResponse.model_validate(
            {"verdicts": [], "noise": "extra"}
        )


def test_rejects_unknown_verdict_field() -> None:
    with pytest.raises(ValidationError):
        DirectorVerdict.model_validate(
            {
                "twist_id": "11111111-1111-1111-1111-111111111111",
                "decision": "approved",
                "reason": "ok",
                "noise": "extra",
            }
        )


def test_rejects_reason_over_80_chars() -> None:
    with pytest.raises(ValidationError):
        DirectorVerdict.model_validate(
            {
                "twist_id": "11111111-1111-1111-1111-111111111111",
                "decision": "approved",
                "reason": "x" * 81,
            }
        )


def test_rejects_empty_reason() -> None:
    with pytest.raises(ValidationError):
        DirectorVerdict.model_validate(
            {
                "twist_id": "11111111-1111-1111-1111-111111111111",
                "decision": "approved",
                "reason": "",
            }
        )


def test_rejects_unknown_decision() -> None:
    with pytest.raises(ValidationError):
        DirectorVerdict.model_validate(
            {
                "twist_id": "11111111-1111-1111-1111-111111111111",
                "decision": "maybe",
                "reason": "ok",
            }
        )


def test_rejects_non_uuid_twist_id() -> None:
    with pytest.raises(ValidationError):
        DirectorVerdict.model_validate(
            {
                "twist_id": "not-a-uuid",
                "decision": "approved",
                "reason": "ok",
            }
        )


# ---------------------------------------------------------------------------
# Schema parity with the JSON-Schema contract
# ---------------------------------------------------------------------------


def test_schema_parity_required_fields() -> None:
    """The verdict item required fields agree with the contract."""
    contract = _contract()
    contract_required = set(
        contract["properties"]["verdicts"]["items"]["required"]
    )
    pydantic_schema = DirectorVerdict.model_json_schema()
    pydantic_required = set(pydantic_schema["required"])
    assert pydantic_required == contract_required


def test_schema_parity_decision_enum() -> None:
    """The decision enum is identical (order-insensitive)."""
    contract = _contract()
    contract_enum = set(
        contract["properties"]["verdicts"]["items"]["properties"]["decision"][
            "enum"
        ]
    )
    pydantic_schema = DirectorVerdict.model_json_schema()
    # Pydantic emits Literal[] as an enum at the field's schema level.
    decision_field = pydantic_schema["properties"]["decision"]
    pydantic_enum = set(decision_field["enum"])
    assert pydantic_enum == contract_enum


def test_schema_parity_reason_max_length() -> None:
    contract = _contract()
    contract_max = (
        contract["properties"]["verdicts"]["items"]["properties"]["reason"][
            "maxLength"
        ]
    )
    pydantic_schema = DirectorVerdict.model_json_schema()
    pydantic_max = pydantic_schema["properties"]["reason"]["maxLength"]
    assert pydantic_max == contract_max == 80


def test_schema_parity_additional_properties_forbidden() -> None:
    """Both the contract and Pydantic close the verdict object to extras."""
    contract = _contract()
    contract_additional = contract["properties"]["verdicts"]["items"][
        "additionalProperties"
    ]
    pydantic_schema = DirectorVerdict.model_json_schema()
    # Pydantic with extra="forbid" emits additionalProperties=false.
    pydantic_additional = pydantic_schema.get("additionalProperties")
    assert contract_additional is False
    assert pydantic_additional is False
