"""Pytest fixtures: invite rows for integration tests.

Module 002 / Task T-003.

Fixtures:
  unused_invite   — status='unused', valid for 7 days.
  redeemed_invite — status='redeemed', with a temporary user as redeemer.

Both skip automatically when DATABASE_URL is the conftest placeholder and
clean up their rows (+ any dependent users) on teardown.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.fixtures import require_real_db_url


@pytest.fixture
async def unused_invite() -> AsyncGenerator[dict[str, Any], None]:
    """Invite con status='unused', vence en 7 días."""
    url = require_real_db_url()
    engine = create_async_engine(url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )

    code = "AAAA-AAAB"
    expires_at = datetime.now(UTC) + timedelta(days=7)

    async with factory() as session:
        await session.execute(
            sa.text(
                "INSERT INTO invites (code, issued_by, expires_at, status, note) "
                "VALUES (:code, 'test-fixture', :expires_at, 'unused',"
                " 'fixture: unused_invite')"
            ),
            {"code": code, "expires_at": expires_at},
        )
        await session.commit()

    yield {"code": code, "expires_at": expires_at, "status": "unused"}

    async with factory() as session:
        await session.execute(
            sa.text("DELETE FROM invites WHERE code = :code"),
            {"code": code},
        )
        await session.commit()

    await engine.dispose()


@pytest.fixture
async def redeemed_invite() -> AsyncGenerator[dict[str, Any], None]:
    """Invite con status='redeemed'. Crea un user temporal como redeemer.

    Limpia el user y el invite al finalizar.
    """
    url = require_real_db_url()
    engine = create_async_engine(url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )

    code = "AAAA-AAAC"
    device_token = "b" * 64  # 64-char hex string, valid for the device_token CHECK
    expires_at = datetime.now(UTC) + timedelta(days=7)
    redeemed_at = datetime.now(UTC)

    user_id: int
    async with factory() as session:
        # 1. Invite como 'unused' para satisfacer FK de users.invite_code
        await session.execute(
            sa.text(
                "INSERT INTO invites (code, issued_by, expires_at, status, note) "
                "VALUES (:code, 'test-fixture', :expires_at, 'unused',"
                " 'fixture: redeemed_invite')"
            ),
            {"code": code, "expires_at": expires_at},
        )
        # 2. User temporal (display_name y device_token válidos)
        result = await session.execute(
            sa.text(
                "INSERT INTO users (display_name, invite_code, device_token) "
                "VALUES ('FixtureRedeemer', :code, :token) RETURNING id"
            ),
            {"code": code, "token": device_token},
        )
        user_id = int(result.scalar_one())
        # 3. Marcar invite como redeemed (CHECK: redeemed_at NOT NULL ↔ status=redeemed)
        await session.execute(
            sa.text(
                "UPDATE invites "
                "SET status='redeemed', redeemed_at=:redeemed_at, redeemed_by_user=:uid "
                "WHERE code=:code"
            ),
            {"redeemed_at": redeemed_at, "uid": user_id, "code": code},
        )
        await session.commit()

    yield {
        "code": code,
        "expires_at": expires_at,
        "redeemed_at": redeemed_at,
        "status": "redeemed",
        "redeemed_by_user": user_id,
    }

    async with factory() as session:
        # users.invite_code FK → invites.code: borrar user antes que el invite
        await session.execute(
            sa.text("DELETE FROM users WHERE invite_code = :code"),
            {"code": code},
        )
        await session.execute(
            sa.text("DELETE FROM invites WHERE code = :code"),
            {"code": code},
        )
        await session.commit()

    await engine.dispose()
