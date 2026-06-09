"""cycles + state_transitions tables.

Module 003 / Task T-002.

Introduces:
  - ``cycles``           — one row per (season, calendar-day in ART); advisory
                           lock anchor; state CHECK enforces the FSM alphabet.
  - ``state_transitions`` — append-only audit + idempotency table.

Key idempotency constraint:
  ``uniq_st_trigger`` — PARTIAL UNIQUE on (cycle_id, to_state, trigger_id)
  WHERE trigger_id IS NOT NULL.  Duplicate INSERTs from GitHub Actions retries
  conflict silently (ON CONFLICT DO NOTHING) and are translated to 200
  ``already_applied`` by the executor.

Depends on revision 0004 (seasons_chapters).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# FSM states (must match cycle_fsm.py)
_VALID_STATES = (
    "'PENDING_RELEASE','ESTRENO','RECEPCION_IDEAS',"
    "'FILTERING','VOTACION','GENERACION','FAILED'"
)

# triggered_by vocabulary
_VALID_TRIGGERED_BY = "'cron','admin','retry','side_effect','watchdog'"


def upgrade() -> None:
    # ------------------------------------------------------------------
    # cycles
    # ------------------------------------------------------------------
    op.create_table(
        "cycles",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "season_id",
            sa.BigInteger,
            sa.ForeignKey("seasons.id"),
            nullable=False,
        ),
        sa.Column(
            "chapter_id",
            sa.BigInteger,
            sa.ForeignKey("chapters.id"),
            nullable=False,
        ),
        sa.Column(
            "next_chapter_id",
            sa.BigInteger,
            sa.ForeignKey("chapters.id"),
            nullable=True,
        ),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column(
            "state_entered_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("cycle_date", sa.Date, nullable=False),
        sa.CheckConstraint(
            f"state IN ({_VALID_STATES})",
            name="ck_cycles_state",
        ),
        sa.UniqueConstraint(
            "season_id", "cycle_date", name="uq_cycles_season_date"
        ),
    )
    op.create_index("idx_cycles_state", "cycles", ["state"])

    # ------------------------------------------------------------------
    # state_transitions
    # ------------------------------------------------------------------
    op.create_table(
        "state_transitions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "cycle_id",
            sa.BigInteger,
            sa.ForeignKey("cycles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_state", sa.Text, nullable=False),
        sa.Column("to_state", sa.Text, nullable=False),
        sa.Column("triggered_by", sa.Text, nullable=False),
        sa.Column("trigger_id", sa.Text, nullable=True),
        sa.Column("payload_json", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"triggered_by IN ({_VALID_TRIGGERED_BY})",
            name="ck_st_triggered_by",
        ),
    )
    # Composite index for health/cycle recent-transitions query (DESC on created_at).
    # Uses raw SQL because SQLAlchemy's op.create_index does not support DESC
    # expressions on older Alembic versions.
    op.execute(
        "CREATE INDEX idx_st_cycle_recent "
        "ON state_transitions(cycle_id, created_at DESC)"
    )
    # Partial unique index — idempotency anchor for cron replay protection.
    op.execute(
        "CREATE UNIQUE INDEX uniq_st_trigger "
        "ON state_transitions(cycle_id, to_state, trigger_id) "
        "WHERE trigger_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uniq_st_trigger")
    op.execute("DROP INDEX IF EXISTS idx_st_cycle_recent")
    op.drop_table("state_transitions")
    op.drop_index("idx_cycles_state", table_name="cycles")
    op.drop_table("cycles")
