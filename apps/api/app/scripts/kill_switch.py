"""CLI: kill-switch — activate or deactivate the FSM kill-switch via admin API.

Module 003 / Task T-021.

Usage (via pnpm):
    pnpm kill-switch -- --on --reason "rebuild bible"
    pnpm kill-switch -- --off

Usage (direct):
    uv run python -m app.scripts.kill_switch --on [--reason TEXT]
    uv run python -m app.scripts.kill_switch --off

Options:
    --on                Activate the kill-switch.
    --off               Deactivate the kill-switch.
    --reason TEXT       Optional human-readable reason (only used with --on).
    --api-url URL       API base URL (default: http://localhost:8000)

Requires:
    ADMIN_TOKEN env var must be set.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import httpx

from app.settings import get_settings

# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kill-switch",
        description="Activa o desactiva el kill-switch del FSM de AI Plot Twist.",
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--on",
        dest="on",
        action="store_true",
        default=None,
        help="Activar el kill-switch.",
    )
    group.add_argument(
        "--off",
        dest="on",
        action="store_false",
        help="Desactivar el kill-switch.",
    )
    p.add_argument(
        "--reason",
        default=None,
        metavar="TEXT",
        help="Razón opcional (usada con --on).",
    )
    p.add_argument(
        "--api-url",
        default="http://localhost:8000",
        metavar="URL",
        help="URL base del API (default: http://localhost:8000)",
    )
    return p


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _run(
    args: argparse.Namespace,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Toggle the kill-switch and return the parsed JSON response.

    Parameters
    ----------
    args:
        Parsed CLI arguments.
    client:
        Optional pre-built ``httpx.Client`` (injected by tests).
    """
    settings = get_settings()

    if not settings.admin_token:
        print("ERROR: ADMIN_TOKEN no está configurado.", file=sys.stderr)
        sys.exit(1)

    url = f"{args.api_url.rstrip('/')}/api/v1/internal/kill-switch"
    headers: dict[str, str] = {
        "Authorization": f"Bearer {settings.admin_token}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "on": bool(args.on),
        "reason": args.reason,
    }

    _client = client or httpx.Client(timeout=30.0)
    close_after = client is None

    try:
        r = _client.post(url, json=payload, headers=headers)
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


def _print_result(result: dict[str, Any]) -> None:
    status = result.get("status", "?")
    reason = result.get("reason")
    print(f"\nKill-switch: {status}")
    if reason:
        print(f"Razón       : {reason}")


def main() -> None:
    args = build_parser().parse_args()
    result = _run(args)
    _print_result(result)


if __name__ == "__main__":
    main()
