"""One-shot recovery script: bootstrap cycle 2 in RECEPCION_IDEAS.

Executed once on 2026-06-13 after the ESTRENO→RECEPCION auto-tick bug
(see fix(003) commit 34d9f35). Cycle 1 stays as a FAILED historical
row; cycle 2 becomes the new "active" because get_active() orders by
cycle_date DESC.

Run inside Fly VM:
    fly ssh console --app ai-plot-twist -C "sh -c 'cat > /tmp/recovery_cycle2.py'" < scripts/recovery_cycle2.py
    fly ssh console --app ai-plot-twist -C "python /tmp/recovery_cycle2.py"
"""

from __future__ import annotations

import asyncio
import os

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine


async def main() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.begin() as conn:
            print("=== Pre-state ===")
            pre_cycles = (await conn.execute(
                sa.text(
                    "SELECT id, state, cycle_date, state_entered_at "
                    "FROM cycles ORDER BY cycle_date DESC, id DESC"
                )
            )).mappings().all()
            for r in pre_cycles:
                print(f"  cycle id={r['id']} state={r['state']} "
                      f"date={r['cycle_date']} entered={r['state_entered_at']}")

            twist_count = (await conn.execute(
                sa.text("SELECT COUNT(*) FROM twists WHERE chapter_id = 1")
            )).scalar_one()
            print(f"  twists on chapter 1: {twist_count}")

            if int(twist_count) > 0:
                print("ABORT: chapter 1 has twists; reusing it for cycle 2 "
                      "is unsafe. Investigate manually.")
                return

            already = (await conn.execute(
                sa.text(
                    "SELECT COUNT(*) FROM cycles WHERE cycle_date = "
                    "'2026-06-13'"
                )
            )).scalar_one()
            if int(already) > 0:
                print("ABORT: a cycle with cycle_date=2026-06-13 already "
                      "exists. Recovery already happened?")
                return

            print("\n=== Inserting cycle 2 ===")
            new_row = (await conn.execute(
                sa.text(
                    "INSERT INTO cycles "
                    "(season_id, chapter_id, state, cycle_date, "
                    " state_entered_at) "
                    "VALUES (1, 1, 'RECEPCION_IDEAS', '2026-06-13', "
                    "        '2026-06-13 15:01:00+00') "
                    "RETURNING id, state, cycle_date, state_entered_at"
                )
            )).mappings().one()
            print(f"  inserted cycle id={new_row['id']} "
                  f"state={new_row['state']} date={new_row['cycle_date']} "
                  f"entered={new_row['state_entered_at']}")

            print("\n=== Post-state ===")
            post_cycles = (await conn.execute(
                sa.text(
                    "SELECT id, state, cycle_date, state_entered_at "
                    "FROM cycles ORDER BY cycle_date DESC, id DESC"
                )
            )).mappings().all()
            for idx, r in enumerate(post_cycles):
                marker = " <- active" if idx == 0 else ""
                print(f"  cycle id={r['id']} state={r['state']} "
                      f"date={r['cycle_date']} entered={r['state_entered_at']}"
                      f"{marker}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
