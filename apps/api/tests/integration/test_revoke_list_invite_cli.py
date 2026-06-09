"""Integration tests: revoke_invite + list_invites CLIs.

Module 002 / Tasks T-012 + T-013.

All DB tests skip when DATABASE_URL is the conftest placeholder.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.scripts.list_invites import _print_json
from app.scripts.list_invites import _run as list_run
from app.scripts.list_invites import build_parser as list_parser
from app.scripts.revoke_invite import _run as revoke_run
from app.scripts.revoke_invite import build_parser as revoke_parser

# ---------------------------------------------------------------------------
# revoke_invite — parser
# ---------------------------------------------------------------------------


def test_revoke_parser_requires_code() -> None:
    import pytest
    with pytest.raises(SystemExit):
        revoke_parser().parse_args([])


def test_revoke_parser_parses_code() -> None:
    args = revoke_parser().parse_args(["AAAA-AAAB"])
    assert args.code == "AAAA-AAAB"
    assert args.allow_prod is False


# ---------------------------------------------------------------------------
# revoke_invite — happy path (real DB)
# ---------------------------------------------------------------------------


async def test_revoke_changes_status_to_revoked(
    db_session: AsyncSession, unused_invite: dict[str, Any]
) -> None:
    code = unused_invite["code"]
    args = argparse.Namespace(code=code, allow_prod=False)

    await revoke_run(args)

    # Verify via db_session (revoke_run committed separately)
    result = await db_session.execute(
        sa.text("SELECT status FROM invites WHERE code = :c"), {"c": code}
    )
    status = result.scalar_one()
    assert status == "revoked"

    # Restore for fixture teardown (fixture deletes by code — status doesn't matter)


async def test_revoke_invalid_code_format_exits(db_session: AsyncSession) -> None:
    import pytest
    args = argparse.Namespace(code="not-a-code", allow_prod=False)
    with pytest.raises(SystemExit):
        await revoke_run(args)


# ---------------------------------------------------------------------------
# list_invites — parser
# ---------------------------------------------------------------------------


def test_list_parser_defaults() -> None:
    args = list_parser().parse_args([])
    assert args.status is None
    assert args.expired_only is False
    assert args.as_json is False


def test_list_parser_all_flags() -> None:
    args = list_parser().parse_args(["--status", "unused", "--expired-only", "--json"])
    assert args.status == "unused"
    assert args.expired_only is True
    assert args.as_json is True


# ---------------------------------------------------------------------------
# list_invites — happy path (real DB)
# ---------------------------------------------------------------------------


async def test_list_all_includes_fixture_code(
    db_session: AsyncSession, unused_invite: dict[str, Any]
) -> None:
    args = argparse.Namespace(status=None, expired_only=False, as_json=False)
    rows = await list_run(args)
    codes = [r.code for r in rows]
    assert unused_invite["code"] in codes


async def test_list_filter_by_status(
    db_session: AsyncSession,
    unused_invite: dict[str, Any],
    redeemed_invite: dict[str, Any],
) -> None:
    args = argparse.Namespace(status="unused", expired_only=False, as_json=False)
    rows = await list_run(args)
    assert all(r.status == "unused" for r in rows)
    assert unused_invite["code"] in [r.code for r in rows]
    assert redeemed_invite["code"] not in [r.code for r in rows]


async def test_list_expired_only_excludes_future_codes(
    db_session: AsyncSession, unused_invite: dict[str, Any]
) -> None:
    """unused_invite expires in 7 days → should NOT appear in --expired-only."""
    args = argparse.Namespace(status=None, expired_only=True, as_json=False)
    rows = await list_run(args)
    codes = [r.code for r in rows]
    assert unused_invite["code"] not in codes


async def test_list_json_output_is_valid_json(
    db_session: AsyncSession, unused_invite: dict[str, Any], capsys: Any
) -> None:
    args = argparse.Namespace(status=None, expired_only=False, as_json=True)
    rows = await list_run(args)
    _print_json(rows)
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert isinstance(parsed, list)
    codes = [item["code"] for item in parsed]
    assert unused_invite["code"] in codes
