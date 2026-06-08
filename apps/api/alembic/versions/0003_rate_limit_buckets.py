"""rate_limit_buckets

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_buckets",
        sa.Column("bucket_key", sa.Text, nullable=False),
        sa.Column("window_start", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "count",
            sa.Integer,
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.PrimaryKeyConstraint("bucket_key", "window_start"),
        sa.CheckConstraint("count >= 0", name="ck_rate_limit_count_nonneg"),
    )


def downgrade() -> None:
    op.drop_table("rate_limit_buckets")
