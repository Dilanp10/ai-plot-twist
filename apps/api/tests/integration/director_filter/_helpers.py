"""Sync helpers for director_filter integration tests.

Module 006 / Task T-009.

Module name starts with ``_`` so pytest does not collect it as a test
file. Fixtures live in the local ``conftest.py``; this module only
provides plain (non-fixture) helpers shared across the four test files.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.twists_repo import Twist, TwistsRepo

SLUG_PREFIX = "_df-test-"
TODAY = date(2026, 6, 12)
_INVITE_ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"

BIBLE_JSON: dict[str, Any] = {
    "title": "Director Test Season",
    "tone": "noir-ish, suspense moderado",
    "rules": ["no romper la cuarta pared", "evitar deus ex machina"],
}
MANIFEST_JSON: dict[str, Any] = {
    "cliffhanger": "El protagonista descubre que la puerta estaba abierta.",
    "panels": [{"id": 1, "prompt": "exterior — noche"}],
}


def fresh_invite_code() -> str:
    left = "".join(secrets.choice(_INVITE_ALPHA) for _ in range(4))
    right = "".join(secrets.choice(_INVITE_ALPHA) for _ in range(4))
    return f"{left}-{right}"


async def make_user(session: AsyncSession) -> tuple[int, str]:
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


async def make_season_and_chapter(
    session: AsyncSession, suffix: str
) -> tuple[int, int]:
    # Append a short random tag so a previously-aborted run leaving a
    # row behind does not collide with the unique slug index.
    tag = uuid4().hex[:8]
    result = await session.execute(
        sa.text(
            "INSERT INTO seasons "
            "(slug, title, bible_json, started_on, is_active) "
            "VALUES (:slug, 'DF Test Season', "
            "        cast(:bible AS jsonb), :today, FALSE) "
            "RETURNING id"
        ),
        {
            "slug": f"{SLUG_PREFIX}{suffix}-{tag}",
            "bible": json.dumps(BIBLE_JSON),
            "today": TODAY,
        },
    )
    season_id = int(result.scalar_one())

    result = await session.execute(
        sa.text(
            "INSERT INTO chapters "
            "(season_id, day_index, title, synopsis, manifest_json, status) "
            "VALUES (:sid, 1, 'Capítulo 1', 'Synopsis breve', "
            "        cast(:manifest AS jsonb), 'live') "
            "RETURNING id"
        ),
        {"sid": season_id, "manifest": json.dumps(MANIFEST_JSON)},
    )
    chapter_id = int(result.scalar_one())
    return season_id, chapter_id


async def seed_pending_twists(
    session: AsyncSession,
    chapter_id: int,
    user_id: int,
    contents: list[str],
) -> list[Twist]:
    """Insert *contents* as pending twists; commit per row so ASC order
    by ``submitted_at`` is deterministic.
    """
    repo = TwistsRepo(session)
    twists: list[Twist] = []
    for content in contents:
        t = await repo.insert(chapter_id, user_id, content)
        await session.commit()
        twists.append(t)
    return twists


async def fetch_twist_status(
    session: AsyncSession, twist_id: int
) -> tuple[str, str | None, datetime | None]:
    row = (
        await session.execute(
            sa.text(
                "SELECT status, director_reason, reviewed_at "
                "FROM twists WHERE id = :id"
            ),
            {"id": twist_id},
        )
    ).mappings().one()
    return (
        str(row["status"]),
        (
            str(row["director_reason"])
            if row["director_reason"] is not None
            else None
        ),
        row["reviewed_at"],
    )


async def count_pending_for_chapter(
    session: AsyncSession, chapter_id: int
) -> int:
    return int(
        (
            await session.execute(
                sa.text(
                    "SELECT COUNT(*) FROM twists "
                    "WHERE chapter_id = :cid AND status = 'pending_review'"
                ),
                {"cid": chapter_id},
            )
        ).scalar_one()
    )


async def cleanup(
    session: AsyncSession,
    season_id: int,
    *users: tuple[int, str],
) -> None:
    await session.execute(
        sa.text(
            "DELETE FROM twists WHERE chapter_id IN "
            "(SELECT id FROM chapters WHERE season_id = :sid)"
        ),
        {"sid": season_id},
    )
    await session.execute(
        sa.text("DELETE FROM chapters WHERE season_id = :sid"),
        {"sid": season_id},
    )
    await session.execute(
        sa.text("DELETE FROM seasons WHERE id = :sid"), {"sid": season_id}
    )
    for uid, code in users:
        await session.execute(
            sa.text("DELETE FROM users WHERE id = :id"), {"id": uid}
        )
        await session.execute(
            sa.text("DELETE FROM invites WHERE code = :code"),
            {"code": code},
        )
    await session.commit()


def make_verdict(
    public_id: UUID,
    decision: str,
    reason: str,
) -> dict[str, Any]:
    """Build a verdict dict for ``DirectorBatchResponse.model_validate``."""
    return {
        "twist_id": str(public_id),
        "decision": decision,
        "reason": reason,
    }
