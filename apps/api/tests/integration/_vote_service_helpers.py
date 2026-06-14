"""Shared helpers for the test_vote_service*.py integration files.

Module 007 / Task T-005.

Module name starts with ``_`` so pytest does not collect it as a test file.
The helpers create a fully active fixture set — season ``is_active=TRUE``,
chapter ``status='live'``, cycle in ``VOTACION`` — which is the expected
runtime state for ``VoteService.cast`` to succeed.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command

API_DIR = Path(__file__).parent.parent.parent
ALEMBIC_INI = API_DIR / "alembic.ini"

SLUG_PREFIX = "_vs-test-"
TODAY = date(2026, 6, 12)

# VOTACION window: FILTERING (18:00 ART = 21:00 UTC) → GENERACION (21:00 ART = 00:00 UTC next day).
# Pick 22:30 UTC = 19:30 ART so we're solidly inside.
NOW_IN_WINDOW = datetime(2026, 6, 12, 22, 30, tzinfo=UTC)
# 02:00 UTC next day = 23:00 ART — after vote_until (21:00 ART = 00:00 UTC next day).
NOW_AFTER_WINDOW = datetime(2026, 6, 13, 2, 0, tzinfo=UTC)

_INVITE_ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def alembic_cfg(database_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(API_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def fresh_invite_code() -> str:
    """Generate an invite code matching ``ck_invites_code_format``."""
    left = "".join(secrets.choice(_INVITE_ALPHA) for _ in range(4))
    right = "".join(secrets.choice(_INVITE_ALPHA) for _ in range(4))
    return f"{left}-{right}"


async def make_user(session: AsyncSession) -> tuple[int, str]:
    """Insert a fresh invite + user; return ``(user_id, invite_code)``."""
    code = fresh_invite_code()
    expires_at = datetime.now(UTC) + timedelta(days=7)
    await session.execute(
        sa.text(
            "INSERT INTO invites (code, issued_by, expires_at, status) "
            "VALUES (:code, 'test', :expires_at, 'unused')"
        ),
        {"code": code, "expires_at": expires_at},
    )
    result = await session.execute(
        sa.text(
            "INSERT INTO users (display_name, invite_code, device_token) "
            "VALUES ('TmpUser', :code, :token) "
            "RETURNING id"
        ),
        {"code": code, "token": (uuid4().hex * 2)[:64]},
    )
    return int(result.scalar_one()), code


async def make_active_votacion_setup(
    session: AsyncSession, suffix: str
) -> tuple[int, int, UUID]:
    """Create active season + live chapter + cycle in VOTACION.

    Returns ``(season_id, chapter_id, chapter_public_id)``.
    """
    await session.execute(
        sa.text("UPDATE seasons SET is_active = FALSE WHERE is_active = TRUE")
    )

    result = await session.execute(
        sa.text(
            "INSERT INTO seasons "
            "(slug, title, bible_json, started_on, is_active) "
            "VALUES (:slug, 'Vote Test', '{}', :today, TRUE) "
            "RETURNING id"
        ),
        {"slug": f"{SLUG_PREFIX}{suffix}", "today": TODAY},
    )
    season_id = int(result.scalar_one())

    # released 3h before NOW_IN_WINDOW (= 12:00 ART on TODAY).
    released_at = NOW_IN_WINDOW - timedelta(hours=10, minutes=30)
    result = await session.execute(
        sa.text(
            "INSERT INTO chapters "
            "(season_id, day_index, title, synopsis, manifest_json, status, released_at) "
            "VALUES (:sid, 1, 'Chap', 'Syn', '{}', 'live', :released) "
            "RETURNING id, public_id"
        ),
        {"sid": season_id, "released": released_at},
    )
    row = result.mappings().one()
    chapter_id = int(row["id"])
    chapter_public_id = UUID(str(row["public_id"]))

    # Entered VOTACION at 18:00 ART = 21:00 UTC (1.5h before NOW_IN_WINDOW).
    state_entered_at = NOW_IN_WINDOW - timedelta(hours=1, minutes=30)
    await session.execute(
        sa.text(
            "INSERT INTO cycles "
            "(season_id, chapter_id, state, cycle_date, state_entered_at) "
            "VALUES (:sid, :cid, 'VOTACION', :today, :entered)"
        ),
        {
            "sid": season_id,
            "cid": chapter_id,
            "today": TODAY,
            "entered": state_entered_at,
        },
    )
    return season_id, chapter_id, chapter_public_id


async def make_approved_twist(
    session: AsyncSession, chapter_id: int, user_id: int, content: str
) -> tuple[int, UUID]:
    """Insert an approved twist; return ``(id, public_id)``."""
    result = await session.execute(
        sa.text(
            "INSERT INTO twists "
            "(chapter_id, user_id, content, status) "
            "VALUES (:cid, :uid, :content, 'approved') "
            "RETURNING id, public_id"
        ),
        {"cid": chapter_id, "uid": user_id, "content": content},
    )
    row = result.mappings().one()
    return int(row["id"]), UUID(str(row["public_id"]))


async def cleanup(
    session: AsyncSession,
    season_id: int,
    *users: tuple[int, str],
) -> None:
    """Delete fixtures respecting FK order.

    state_transitions → cycles → votes → twists → chapters → seasons → users → invites.
    """
    await session.execute(
        sa.text(
            "DELETE FROM state_transitions WHERE cycle_id IN "
            "(SELECT id FROM cycles WHERE season_id = :sid)"
        ),
        {"sid": season_id},
    )
    await session.execute(
        sa.text("DELETE FROM cycles WHERE season_id = :sid"), {"sid": season_id}
    )
    await session.execute(
        sa.text(
            "DELETE FROM votes WHERE chapter_id IN "
            "(SELECT id FROM chapters WHERE season_id = :sid)"
        ),
        {"sid": season_id},
    )
    await session.execute(
        sa.text(
            "DELETE FROM twists WHERE chapter_id IN "
            "(SELECT id FROM chapters WHERE season_id = :sid)"
        ),
        {"sid": season_id},
    )
    await session.execute(
        sa.text("DELETE FROM chapters WHERE season_id = :sid"), {"sid": season_id}
    )
    await session.execute(
        sa.text("DELETE FROM seasons WHERE id = :sid"), {"sid": season_id}
    )
    for uid, code in users:
        await session.execute(
            sa.text("DELETE FROM users WHERE id = :id"), {"id": uid}
        )
        await session.execute(
            sa.text("DELETE FROM invites WHERE code = :code"), {"code": code}
        )
    await session.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def database_url() -> str:
    from tests.conftest import _is_placeholder_database_url

    url = os.environ.get("DATABASE_URL", "")
    if not url or _is_placeholder_database_url(url):
        pytest.skip("DATABASE_URL no apunta a una base real.")
    return url


@pytest.fixture(scope="module", autouse=True)
def _ensure_migrated(database_url: str) -> None:
    asyncio.get_event_loop().run_until_complete(
        asyncio.to_thread(command.upgrade, alembic_cfg(database_url), "head")
    )


@pytest.fixture
async def session_factory(
    database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False
    )
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def setup_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as s:
        yield s
