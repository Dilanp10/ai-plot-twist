"""Integration tests: UsersRepo.

Module 002 / Task T-009.

Uses ``db_session`` (rollback on teardown) and ``active_user`` /
``banned_user`` fixtures (committed rows, cleaned up by fixture teardown).

All tests skip when DATABASE_URL is the conftest placeholder.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.device_secret import mint
from app.domain.invites import InviteCode
from app.infra.users_repo import UsersRepo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_invite(session: AsyncSession, code: str) -> None:
    """Insert a minimal 'unused' invite so FK constraint is satisfied."""
    from datetime import UTC, datetime, timedelta

    import sqlalchemy as sa

    await session.execute(
        sa.text(
            "INSERT INTO invites (code, issued_by, expires_at, status) "
            "VALUES (:code, 'test', :exp, 'unused')"
        ),
        {"code": code, "exp": datetime.now(UTC) + timedelta(days=7)},
    )


# ---------------------------------------------------------------------------
# create()
# ---------------------------------------------------------------------------


async def test_create_returns_user_row(db_session: AsyncSession) -> None:
    invite = InviteCode.generate()
    await _insert_invite(db_session, str(invite))
    _, token_hash = mint()

    repo = UsersRepo(db_session)
    row = await repo.create(
        display_name="Bienvenido",
        invite_code=invite,
        device_token_hash=token_hash,
    )

    assert row.display_name == "Bienvenido"
    assert row.invite_code == str(invite)
    assert row.device_token == token_hash
    assert row.is_banned is False
    assert row.public_id is not None
    assert row.id > 0


async def test_create_sets_timestamps(db_session: AsyncSession) -> None:
    from datetime import UTC, datetime

    invite = InviteCode.generate()
    await _insert_invite(db_session, str(invite))
    _, token_hash = mint()

    row = await UsersRepo(db_session).create("Nuevo", invite, token_hash)

    now = datetime.now(UTC)
    # created_at should be recent (within 5 s)
    assert abs((now - row.created_at.replace(tzinfo=UTC)).total_seconds()) < 5


# ---------------------------------------------------------------------------
# get_by_public_id()
# ---------------------------------------------------------------------------


async def test_get_by_public_id_existing(
    db_session: AsyncSession, active_user: dict[str, Any]
) -> None:
    from uuid import UUID

    public_id = UUID(str(active_user["public_id"]))
    row = await UsersRepo(db_session).get_by_public_id(public_id)

    assert row is not None
    assert row.public_id == public_id
    assert row.display_name == active_user["display_name"]
    assert row.is_banned is False


async def test_get_by_public_id_banned(
    db_session: AsyncSession, banned_user: dict[str, Any]
) -> None:
    from uuid import UUID

    public_id = UUID(str(banned_user["public_id"]))
    row = await UsersRepo(db_session).get_by_public_id(public_id)

    assert row is not None
    assert row.is_banned is True


async def test_get_by_public_id_missing(db_session: AsyncSession) -> None:
    row = await UsersRepo(db_session).get_by_public_id(uuid4())
    assert row is None


# ---------------------------------------------------------------------------
# get_by_device_token()
# ---------------------------------------------------------------------------


async def test_get_by_device_token_existing(
    db_session: AsyncSession, active_user: dict[str, Any]
) -> None:
    token_hash = active_user["device_token"]
    row = await UsersRepo(db_session).get_by_device_token(token_hash)

    assert row is not None
    assert row.device_token == token_hash


async def test_get_by_device_token_missing(db_session: AsyncSession) -> None:
    row = await UsersRepo(db_session).get_by_device_token("0" * 64)
    assert row is None


# ---------------------------------------------------------------------------
# touch_last_seen()
# ---------------------------------------------------------------------------


async def test_touch_last_seen_updates_timestamp(
    db_session: AsyncSession, active_user: dict[str, Any]
) -> None:
    from datetime import UTC, datetime
    from uuid import UUID

    public_id = UUID(str(active_user["public_id"]))
    repo = UsersRepo(db_session)

    await repo.touch_last_seen(public_id)

    row = await repo.get_by_public_id(public_id)
    assert row is not None
    # last_seen_at should now be very recent
    now = datetime.now(UTC)
    assert abs((now - row.last_seen_at.replace(tzinfo=UTC)).total_seconds()) < 5
