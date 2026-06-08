"""``POST /api/v1/internal/transition`` — HMAC-stubbed FSM transition trigger.

In module 001 this endpoint is a **stub**: it validates HMAC + timestamp
drift (delegated to ``verify_hmac_tick``) + payload shape, rejects in-process
replays of the same ``trigger_id``, and returns ``202 {accepted, noop:true}``.
Module 003 will replace the body with real state-mutation logic and persist
``trigger_id`` to ``state_transitions(UNIQUE)`` for cross-process replay
protection.

Why an in-process cache instead of relying on the DB? In module 001 there
is no ``state_transitions`` table yet. The cache is a stopgap that satisfies
constitution Gate 2 ("can every cron-triggered job be re-fired with the same
trigger_id without producing duplicate side-effects?") within a single
process lifetime. The bound (10 000 entries) is sized for ~3 ticks/day
times several years of run-time and is FIFO-evicted.
"""

from __future__ import annotations

import collections
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, ValidationError

from app.errors import ProblemDetail
from app.logging import get_logger
from app.middleware.hmac_tick import verify_hmac_tick

_log = get_logger(__name__)

TRIGGER_CACHE_MAX_ENTRIES = 10_000


# ---------------------------------------------------------------------------
# In-process replay cache
# ---------------------------------------------------------------------------


class TriggerIdReplayCache:
    """Bounded FIFO cache of ``trigger_id`` values seen by this process.

    Behaviour: when ``add()`` would exceed ``max_entries``, the oldest
    inserted id is evicted (FIFO order, tracked by ``OrderedDict`` insertion
    order). Membership is O(1).

    Module 003 supersedes this with a persistent UNIQUE constraint on
    ``state_transitions(cycle_id, to_state, trigger_id)``.
    """

    def __init__(self, max_entries: int = TRIGGER_CACHE_MAX_ENTRIES) -> None:
        self._seen: collections.OrderedDict[str, None] = collections.OrderedDict()
        self._max_entries = max_entries

    def contains(self, trigger_id: str) -> bool:
        return trigger_id in self._seen

    def add(self, trigger_id: str) -> None:
        while len(self._seen) >= self._max_entries:
            self._seen.popitem(last=False)
        self._seen[trigger_id] = None


# Module-level singleton. Tests override it via ``app.dependency_overrides``.
_trigger_cache = TriggerIdReplayCache()


def get_trigger_cache() -> TriggerIdReplayCache:
    """FastAPI dependency: return the process-wide replay cache."""
    return _trigger_cache


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class TickPayload(BaseModel):
    """Validated tick payload.

    The HMAC dependency already enforces that ``ts`` is an integer; this
    model adds the **semantic** layer: ``to`` is one of the four enumerated
    FSM target states (per spec §4.1), and ``trigger_id`` is a non-trivial
    string (4-128 chars matches the OpenAPI contract).
    """

    to: Literal["ESTRENO", "FILTERING", "GENERACION", "WATCHDOG"]
    ts: int
    trigger_id: str = Field(min_length=4, max_length=128)


class TransitionAccepted(BaseModel):
    """202 response body. ``noop`` becomes ``False`` in module 003."""

    status: Literal["accepted"] = "accepted"
    noop: bool = True


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/internal", tags=["internal"])


def _summarize_validation_errors(exc: ValidationError) -> str:
    """Build a leak-safe one-line summary of Pydantic errors.

    Returns ``"field.subfield: type; other.field: type"``. Field names are
    public (documented in the contract), the error ``type`` codes are
    Pydantic-defined identifiers — never user input. No values leak.
    """
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        parts.append(f"{loc}: {err['type']}")
    return "; ".join(parts)


@router.post(
    "/transition",
    operation_id="postInternalTransition",
    status_code=202,
    response_model=TransitionAccepted,
    summary="HMAC-signed FSM transition trigger (stub in module 001)",
)
async def post_transition(
    raw_payload: dict[str, Any] = Depends(verify_hmac_tick),
    cache: TriggerIdReplayCache = Depends(get_trigger_cache),
) -> TransitionAccepted:
    """Validate semantic shape + replay-protect, then return a noop accept.

    On replay (same ``trigger_id`` within this process), responds **409**
    ``trigger_replayed`` per the contract.
    """
    # ── Semantic validation (HMAC dep only checked ``ts``) ────────────────
    try:
        payload = TickPayload.model_validate(raw_payload)
    except ValidationError as exc:
        raise ProblemDetail(
            status=422,
            code="bad_payload",
            title="Invalid payload",
            detail=_summarize_validation_errors(exc),
        ) from exc

    # ── Replay protection (constitution Gate 2) ───────────────────────────
    if cache.contains(payload.trigger_id):
        _log.warning(
            "internal_transition_replay_rejected",
            trigger_id=payload.trigger_id,
            to=payload.to,
        )
        raise ProblemDetail(
            status=409,
            code="trigger_replayed",
            title="Trigger already processed",
            detail="trigger_id was seen before in this process lifetime.",
        )
    cache.add(payload.trigger_id)

    _log.info(
        "internal_transition_stub",
        to=payload.to,
        trigger_id=payload.trigger_id,
        ts=payload.ts,
        outcome="accepted_noop",
    )

    return TransitionAccepted()
