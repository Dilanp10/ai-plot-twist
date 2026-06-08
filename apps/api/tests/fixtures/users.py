"""Pytest fixtures: user rows for integration tests.

Module 002 / Task T-003.

Fixtures:
  active_user — user activo (is_banned=False), con invite redimido.
  banned_user — user baneado (is_banned=True), con invite redimido.

Cada fixture ejecuta la transacción de redención completa (invite + user +
update invite) y limpia en teardown. Skippean si DATABASE_URL es placeholder.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.fixtures import require_real_db_url


async def _insert_user_with_invite(
    factory: async_sessionmaker[AsyncSession],
    invite_code: str,
    display_name: str,
    device_token: str,
    is_banned: bool = False,
) -> tuple[int, Any]:
    """Transacción atómica: inserta invite + user y marca el invite como redeemed.

    Devuelve (user_id, public_id).
    """
    expires_at = datetime.now(UTC) + timedelta(days=7)
    async with factory() as session:
        await session.execute(
            sa.text(
                "INSERT INTO invites (code, issued_by, expires_at, status, note) "
                "VALUES (:code, 'test-fixture', :expires_at, 'unused',"
                " 'fixture: user insert')"
            ),
            {"code": invite_code, "expires_at": expires_at},
        )
        banned_sql = ", is_banned" if is_banned else ""
        banned_val = ", TRUE" if is_banned else ""
        result = await session.execute(
            sa.text(
                f"INSERT INTO users (display_name, invite_code, device_token{banned_sql}) "
                f"VALUES (:name, :code, :token{banned_val}) RETURNING id, public_id"
            ),
            {"name": display_name, "code": invite_code, "token": device_token},
        )
        row = result.mappings().one()
        user_id: int = int(row["id"])
        public_id: Any = row["public_id"]
        await session.execute(
            sa.text(
                "UPDATE invites "
                "SET status='redeemed', redeemed_at=now(), redeemed_by_user=:uid "
                "WHERE code=:code"
            ),
            {"uid": user_id, "code": invite_code},
        )
        await session.commit()
    return user_id, public_id


async def _delete_user_and_invite(
    factory: async_sessionmaker[AsyncSession],
    user_id: int,
    invite_code: str,
) -> None:
    async with factory() as session:
        await session.execute(
            sa.text("DELETE FROM users WHERE id = :id"),
            {"id": user_id},
        )
        await session.execute(
            sa.text("DELETE FROM invites WHERE code = :code"),
            {"code": invite_code},
        )
        await session.commit()


@pytest.fixture
async def active_user() -> AsyncGenerator[dict[str, Any], None]:
    """User activo (is_banned=False) con invite redimido. Limpia al finalizar."""
    url = require_real_db_url()
    engine = create_async_engine(url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )

    invite_code = "AAAA-AAAD"
    device_token = "c" * 64
    display_name = "UsuarioActivo"

    user_id, public_id = await _insert_user_with_invite(
        factory, invite_code, display_name, device_token, is_banned=False
    )

    yield {
        "id": user_id,
        "public_id": public_id,
        "display_name": display_name,
        "invite_code": invite_code,
        "device_token": device_token,
        "is_banned": False,
    }

    await _delete_user_and_invite(factory, user_id, invite_code)
    await engine.dispose()


@pytest.fixture
async def banned_user() -> AsyncGenerator[dict[str, Any], None]:
    """User baneado (is_banned=True) con invite redimido. Limpia al finalizar."""
    url = require_real_db_url()
    engine = create_async_engine(url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )

    invite_code = "AAAA-AAAE"
    device_token = "d" * 64
    display_name = "UsuarioBaneado"

    user_id, public_id = await _insert_user_with_invite(
        factory, invite_code, display_name, device_token, is_banned=True
    )

    yield {
        "id": user_id,
        "public_id": public_id,
        "display_name": display_name,
        "invite_code": invite_code,
        "device_token": device_token,
        "is_banned": True,
    }

    await _delete_user_and_invite(factory, user_id, invite_code)
    await engine.dispose()
