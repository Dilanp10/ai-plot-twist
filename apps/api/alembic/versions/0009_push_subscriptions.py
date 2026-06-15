"""push_subscriptions table for browser Web Push endpoints.

Module 011 / Task T-001.

One row per browser/profile subscription. ``UNIQUE (endpoint)`` is the
UPSERT anchor: re-subscribing from the same browser (after clearing
site data, signing out + back in, or rotating users) reuses the same
endpoint and rebinds it to the current user — see
:mod:`app.infra.push_subscriptions_repo`.

Deletion is **hard** by design (constitution Gate 7 carve-out — see
``docs/adr/0007-push-subscription-hard-delete.md`` once that lands).
The push service guarantees endpoints are opaque + ephemeral, so
preserving deleted rows offers zero downstream value.

Depends on revision 0008 (votes).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column(
            "id",
            sa.BigInteger,
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("endpoint", sa.Text, nullable=False, unique=True),
        sa.Column("p256dh_key", sa.Text, nullable=False),
        sa.Column("auth_key", sa.Text, nullable=False),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_success_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "failure_count",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.CheckConstraint(
            "failure_count >= 0",
            name="ck_push_failure_count_nonneg",
        ),
    )
    op.create_index(
        "idx_push_user",
        "push_subscriptions",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_push_user", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
