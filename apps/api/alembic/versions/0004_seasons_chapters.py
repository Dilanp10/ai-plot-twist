"""seasons + chapters tables.

Module 003 / Task T-001.

Introduces the first two tables of the daily-cycle FSM:
  - ``seasons``  — one active season at a time (partial unique index).
  - ``chapters`` — each chapter belongs to one season; status enum enforced
                   by CHECK constraint; (season_id, day_index) unique.

Depends on revision 0003 (rate_limit_buckets).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # seasons
    # ------------------------------------------------------------------
    op.create_table(
        "seasons",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("slug", sa.Text, nullable=False, unique=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("bible_json", JSONB, nullable=False),
        sa.Column("started_on", sa.Date, nullable=False),
        sa.Column("ended_on", sa.Date, nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    # At most one active season may exist at a time.
    op.execute(
        "CREATE UNIQUE INDEX uniq_one_active_season "
        "ON seasons(is_active) WHERE is_active = TRUE"
    )

    # ------------------------------------------------------------------
    # chapters
    # ------------------------------------------------------------------
    op.create_table(
        "chapters",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "public_id",
            UUID(as_uuid=True),
            nullable=False,
            unique=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "season_id",
            sa.BigInteger,
            sa.ForeignKey("seasons.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day_index", sa.Integer, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("synopsis", sa.Text, nullable=False),
        sa.Column("manifest_json", JSONB, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("released_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "season_id", "day_index", name="uq_chapters_season_day"
        ),
        sa.CheckConstraint(
            "status IN ("
            "'draft','generating','ready','ready_degraded','live','archived'"
            ")",
            name="ck_chapters_status",
        ),
    )
    op.create_index(
        "idx_chapters_status_release",
        "chapters",
        ["status", "released_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_chapters_status_release", table_name="chapters")
    op.drop_table("chapters")
    op.execute("DROP INDEX IF EXISTS uniq_one_active_season")
    op.drop_table("seasons")
