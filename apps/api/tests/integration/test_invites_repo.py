"""Integration tests: InvitesRepo.

Module 002 / Task T-008.

Uses the ``db_session`` fixture (rolls back after each test) and the
``unused_invite`` / ``redeemed_invite`` fixtures (committed rows, cleaned up
by fixture teardown).

All tests skip when DATABASE_URL is the conftest placeholder.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.invites import InviteCode
from app.infra.invites_repo import InvitesRepo


def _fresh_code() -> InviteCode:
    """Generate a random valid InviteCode for tests that need a fresh code."""
    return InviteCode.generate()


# ---------------------------------------------------------------------------
# insert()
# ---------------------------------------------------------------------------


async def test_insert_returns_unused_row(db_session: AsyncSession) -> None:
    code = _fresh_code()
    expires_at = datetime.now(UTC) + timedelta(days=7)
    repo = InvitesRepo(db_session)

    row = await repo.insert(code, expires_at, issued_by="test-runner", note="nota")

    assert row.code == str(code)
    assert row.status == "unused"
    assert row.issued_by == "test-runner"
    assert row.note == "nota"
    assert row.redeemed_at is None
    assert row.redeemed_by_user is None


async def test_insert_without_note(db_session: AsyncSession) -> None:
    code = _fresh_code()
    expires_at = datetime.now(UTC) + timedelta(days=1)
    row = await InvitesRepo(db_session).insert(code, expires_at, issued_by="po")
    assert row.note is None


# ---------------------------------------------------------------------------
# get_for_update()
# ---------------------------------------------------------------------------


async def test_get_for_update_existing_code(
    db_session: AsyncSession, unused_invite: dict[str, Any]
) -> None:
    code = InviteCode.parse(unused_invite["code"])
    row = await InvitesRepo(db_session).get_for_update(code)

    assert row is not None
    assert row.code == str(code)
    assert row.status == "unused"


async def test_get_for_update_missing_code(db_session: AsyncSession) -> None:
    # Use a code that was never inserted
    code = InviteCode.parse("ZZZZ-ZZZP")  # check digit P: sum(25*7)%32=15 → P ✓
    row = await InvitesRepo(db_session).get_for_update(code)
    assert row is None


# ---------------------------------------------------------------------------
# revoke()
# ---------------------------------------------------------------------------


async def test_revoke_changes_status(
    db_session: AsyncSession, unused_invite: dict[str, Any]
) -> None:
    code = InviteCode.parse(unused_invite["code"])
    repo = InvitesRepo(db_session)

    await repo.revoke(code)

    row = await repo.get_for_update(code)
    assert row is not None
    assert row.status == "revoked"


# ---------------------------------------------------------------------------
# mark_redeemed()
# ---------------------------------------------------------------------------


async def test_mark_redeemed_requires_user_id(db_session: AsyncSession) -> None:
    """Insert a fresh invite, then mark it redeemed (needs a user row first).

    We insert the user via raw SQL to avoid coupling on UsersRepo.
    """
    invite_code = _fresh_code()
    expires_at = datetime.now(UTC) + timedelta(days=7)
    repo = InvitesRepo(db_session)

    await repo.insert(invite_code, expires_at, issued_by="test")

    # Insert a minimal user row directly
    result = await db_session.execute(
        __import__("sqlalchemy").text(
            "INSERT INTO users (display_name, invite_code, device_token) "
            "VALUES ('TmpUser', :code, :token) RETURNING id"
        ),
        {"code": str(invite_code), "token": "e" * 64},
    )
    user_id: int = int(result.scalar_one())

    await repo.mark_redeemed(invite_code, user_id)

    row = await repo.get_for_update(invite_code)
    assert row is not None
    assert row.status == "redeemed"
    assert row.redeemed_at is not None
    assert row.redeemed_by_user == user_id


# ---------------------------------------------------------------------------
# list_all()
# ---------------------------------------------------------------------------


async def test_list_all_includes_known_invite(
    db_session: AsyncSession, unused_invite: dict[str, Any]
) -> None:
    rows = await InvitesRepo(db_session).list_all()
    codes = [r.code for r in rows]
    assert unused_invite["code"] in codes


async def test_list_all_includes_redeemed_invite(
    db_session: AsyncSession, redeemed_invite: dict[str, Any]
) -> None:
    rows = await InvitesRepo(db_session).list_all()
    codes = [r.code for r in rows]
    assert redeemed_invite["code"] in codes


async def test_list_all_returns_list(db_session: AsyncSession) -> None:
    rows = await InvitesRepo(db_session).list_all()
    assert isinstance(rows, list)


# ---------------------------------------------------------------------------
# Edge: duplicate code insert raises
# ---------------------------------------------------------------------------


async def test_insert_duplicate_code_raises(db_session: AsyncSession) -> None:
    code = _fresh_code()
    expires_at = datetime.now(UTC) + timedelta(days=7)
    repo = InvitesRepo(db_session)

    await repo.insert(code, expires_at, issued_by="first")

    with pytest.raises(Exception):  # noqa: B017 — DB raises IntegrityError, exact type varies
        await repo.insert(code, expires_at, issued_by="second")
