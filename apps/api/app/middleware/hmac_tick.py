"""HMAC tick verification — FastAPI dependency for ``/internal/*`` endpoints.

Verifies (in order):

1. ``TICK_SECRET`` is configured server-side → otherwise **503**
   ``tick_secret_missing`` (spec edge case: the app must boot anyway, only
   ``/internal/*`` traffic fails).
2. Request carries an ``X-Tick-Signature`` header → otherwise **401**
   ``missing_signature``.
3. ``base64(HMAC-SHA256(body, TICK_SECRET))`` matches the header — compared
   in constant time via :func:`hmac.compare_digest` → otherwise **401**
   ``bad_hmac``.
4. Body is a JSON object with an integer ``ts`` field → otherwise **422**
   ``bad_payload``.
5. ``|now - ts| <= TICK_DRIFT_TOLERANCE_S`` (default 300 s) → otherwise **409**
   ``ts_drift``.

The dependency returns the *parsed* JSON payload as a dict so the route
handler does not have to read the body a second time.

Per Spec Kit constitution Gate 9, all error responses follow RFC 7807
(``application/problem+json``) via :class:`app.errors.ProblemDetail`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import Depends, Header, Request

from app.errors import ProblemDetail
from app.settings import Settings, get_settings

TICK_SIGNATURE_HEADER = "X-Tick-Signature"
TICK_DRIFT_TOLERANCE_S = 300


async def verify_hmac_tick(
    request: Request,
    settings: Settings = Depends(get_settings),
    x_tick_signature: str | None = Header(default=None, alias=TICK_SIGNATURE_HEADER),
) -> dict[str, Any]:
    """Verify an HMAC-signed tick payload and return the parsed body.

    Raises :class:`ProblemDetail` (handled globally by ``problem_handler``)
    on any failure.
    """
    # ── 1. Server config check ─────────────────────────────────────────────
    if not settings.tick_secret:
        raise ProblemDetail(
            status=503,
            code="tick_secret_missing",
            title="Server misconfigured",
            detail="TICK_SECRET is not set on the server.",
        )

    # ── 2. Header presence ────────────────────────────────────────────────
    if not x_tick_signature:
        raise ProblemDetail(
            status=401,
            code="missing_signature",
            title="Missing signature",
            detail=f"{TICK_SIGNATURE_HEADER} header is required.",
        )

    # ── 3. HMAC compare (constant time) ───────────────────────────────────
    body: bytes = await request.body()
    expected_sig = base64.b64encode(
        hmac.new(
            key=settings.tick_secret.encode("utf-8"),
            msg=body,
            digestmod=hashlib.sha256,
        ).digest()
    ).decode("ascii")

    if not hmac.compare_digest(expected_sig, x_tick_signature):
        raise ProblemDetail(
            status=401,
            code="bad_hmac",
            title="Invalid signature",
            detail="HMAC verification failed.",
        )

    # ── 4. Body shape ─────────────────────────────────────────────────────
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ProblemDetail(
            status=422,
            code="bad_payload",
            title="Invalid JSON",
            detail="Request body is not valid JSON.",
        ) from exc

    if not isinstance(payload, dict):
        raise ProblemDetail(
            status=422,
            code="bad_payload",
            title="Invalid payload",
            detail="Body must be a JSON object.",
        )

    ts = payload.get("ts")
    if not isinstance(ts, int) or isinstance(ts, bool):
        # bool is a subclass of int — exclude it explicitly so `ts: true`
        # does not pass as a valid epoch.
        raise ProblemDetail(
            status=422,
            code="bad_payload",
            title="Missing or non-integer ts",
            detail="Field 'ts' must be an integer Unix epoch (seconds).",
        )

    # ── 5. Timestamp drift ────────────────────────────────────────────────
    now = int(time.time())
    if abs(now - ts) > TICK_DRIFT_TOLERANCE_S:
        raise ProblemDetail(
            status=409,
            code="ts_drift",
            title="Timestamp drift",
            detail=(f"|now - ts| > {TICK_DRIFT_TOLERANCE_S}s (now={now}, ts={ts})."),
        )

    return payload
