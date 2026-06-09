"""system_flags table with kill_switch seed row.

Module 003 / Task T-003.

A single-row-by-convention key/value table for ops toggles.
The kill_switch row is seeded here so the executor can always read it
without handling a missing-row case.

Depends on revision 0005 (cycles_transitions).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_flags",
        sa.Column("flag_key", sa.Text, primary_key=True),
        sa.Column("flag_value", JSONB, nullable=False),
        sa.Column("updated_by", sa.Text, nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # Seed the kill-switch in the off position.
    op.execute(
        "INSERT INTO system_flags (flag_key, flag_value, updated_by) "
        "VALUES ('kill_switch', '{\"on\": false, \"reason\": null}', 'migration')"
    )


def downgrade() -> None:
    op.drop_table("system_flags")
