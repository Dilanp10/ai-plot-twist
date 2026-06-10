"""CLI: replay-tick — post a synthetic signed tick to the local API.

Module 003 / Task T-020.

Usage (via pnpm):
    pnpm replay-tick -- --to ESTRENO
    pnpm replay-tick -- --to FILTERING --no-dwell-check

Usage (direct):
    uv run python -m app.scripts.replay_tick --to STATE [OPTIONS]

Options:
    --to STATE          Target FSM state (PENDING_RELEASE | ESTRENO | RECEPCION_IDEAS
                        | FILTERING | VOTACION | GENERACION | FAILED | WATCHDOG)
    --api-url URL       API base URL (default: http://localhost:8000)
    --no-dwell-check    Add X-Dev-Skip-Dwell: 1 header (ignored by prod)

Requires:
    TICK_SECRET env var must be set (same value as the server).

Used for manual recovery from FAILED state.  The trigger_id is always a fresh
``local-replay-<uuid>`` so it is never confused with a real cron tick.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import sys
import time
import uuid
from typing import Any

import httpx

from app.middleware.hmac_tick import TICK_SIGNATURE_HEADER
from app.settings import get_settings

_VALID_STATES = (
    "PENDING_RELEASE",
    "ESTRENO",
    "RECEPCION_IDEAS",
    "FILTERING",
    "VOTACION",
    "GENERACION",
    "FAILED",
    "WATCHDOG",
)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="replay-tick",
        description="Postea un tick sintético firmado al API local.",
    )
    p.add_argument(
        "--to",
        required=True,
        choices=_VALID_STATES,
        metavar="STATE",
        help=f"Estado destino: {', '.join(_VALID_STATES)}",
    )
    p.add_argument(
        "--api-url",
        default="http://localhost:8000",
        metavar="URL",
        help="URL base del API (default: http://localhost:8000)",
    )
    p.add_argument(
        "--no-dwell-check",
        action="store_true",
        help="Agrega X-Dev-Skip-Dwell: 1 (ignorado en prod)",
    )
    return p


# ---------------------------------------------------------------------------
# HMAC helper
# ---------------------------------------------------------------------------


def _sign(body: bytes, secret: str) -> str:
    return base64.b64encode(
        hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    ).decode("ascii")


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _run(
    args: argparse.Namespace,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Post a synthetic tick and return the parsed JSON response.

    Parameters
    ----------
    args:
        Parsed CLI arguments.
    client:
        Optional pre-built ``httpx.Client`` (injected by tests).
    """
    settings = get_settings()

    if not settings.tick_secret:
        print("ERROR: TICK_SECRET no está configurado.", file=sys.stderr)
        sys.exit(1)

    trigger_id = f"local-replay-{uuid.uuid4()}"
    payload: dict[str, Any] = {
        "to": args.to,
        "ts": int(time.time()),
        "trigger_id": trigger_id,
    }
    body = json.dumps(payload).encode("utf-8")
    sig = _sign(body, settings.tick_secret)

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        TICK_SIGNATURE_HEADER: sig,
    }
    if args.no_dwell_check:
        headers["X-Dev-Skip-Dwell"] = "1"

    url = f"{args.api_url.rstrip('/')}/api/v1/internal/transition"

    _client = client or httpx.Client(timeout=30.0)
    close_after = client is None

    try:
        r = _client.post(url, content=body, headers=headers)
    finally:
        if close_after:
            _client.close()

    if r.status_code >= 400:
        print(
            f"ERROR: {r.status_code} {r.text}",
            file=sys.stderr,
        )
        sys.exit(1)

    return dict(r.json())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _print_result(result: dict[str, Any], *, to: str, trigger_id_hint: str) -> None:
    status = result.get("status", "?")
    print(
        f"\nReplay-tick completado:\n"
        f"  to      : {to}\n"
        f"  status  : {status}\n"
        f"  trigger : {trigger_id_hint}\n"
    )
    if "transition_id" in result:
        print(f"  transition_id : {result['transition_id']}")
    if "verdict" in result:
        print(f"  verdict : {result['verdict']}")


def main() -> None:
    args = build_parser().parse_args()
    result = _run(args)
    _print_result(result, to=args.to, trigger_id_hint="local-replay-…")


if __name__ == "__main__":
    main()
