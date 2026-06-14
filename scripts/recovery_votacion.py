"""One-shot: force cycle 2 to VOTACION from FILTERING.

Run from inside the Fly container where DATABASE_URL + JWT_SECRET are set:

    cd /app && .venv/bin/python3 /tmp/recovery_votacion.py
"""
from __future__ import annotations

import asyncio
import sys


async def main() -> None:
    from app.db import get_session_factory
    from app.domain.cycle_executor import transition

    factory = get_session_factory()
    async with factory() as session:
        result = await transition(
            session,
            requested_to="VOTACION",
            triggered_by="admin",
            trigger_id="manual-votacion-recovery-20260613",
            skip_dwell=True,
        )
    print(f"status={result.status} transition_id={result.transition_id}")


asyncio.run(main())
