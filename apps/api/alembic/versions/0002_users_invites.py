"""users + invites

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-08
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invites",
        sa.Column("code", sa.Text, primary_key=True),
        sa.Column("issued_by", sa.Text, nullable=False),
        sa.Column(
            "issued_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("redeemed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("redeemed_by_user", sa.BigInteger, nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.CheckConstraint(
            "code ~ '^[A-Z2-7]{4}-[A-Z2-7]{4}$'",
            name="ck_invites_code_format",
        ),
        sa.CheckConstraint(
            "status IN ('unused','redeemed','revoked','expired')",
            name="ck_invites_status",
        ),
        sa.CheckConstraint(
            "(status = 'redeemed') = (redeemed_at IS NOT NULL)",
            name="ck_invites_redeemed_consistency",
        ),
    )
    op.create_index("idx_invites_status", "invites", ["status"])
    op.create_index(
        "idx_invites_expires",
        "invites",
        ["expires_at"],
        postgresql_where=sa.text("status = 'unused'"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "public_id",
            UUID(as_uuid=True),
            nullable=False,
            unique=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column(
            "invite_code",
            sa.Text,
            sa.ForeignKey("invites.code"),
            nullable=False,
        ),
        sa.Column("device_token", sa.Text, nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "is_banned",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.CheckConstraint(
            "char_length(display_name) BETWEEN 2 AND 24",
            name="ck_users_display_name_len",
        ),
        sa.CheckConstraint(
            "char_length(device_token) = 64",
            name="ck_users_device_token_len",
        ),
    )
    op.create_index("idx_users_invite_code", "users", ["invite_code"])
    op.create_index(
        "idx_users_last_seen",
        "users",
        [sa.text("last_seen_at DESC")],
        postgresql_where=sa.text("is_banned = FALSE"),
    )

    op.create_foreign_key(
        "fk_invites_redeemed_by_user",
        "invites",
        "users",
        ["redeemed_by_user"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_invites_redeemed_by_user", "invites", type_="foreignkey")
    op.drop_index("idx_users_last_seen", table_name="users")
    op.drop_index("idx_users_invite_code", table_name="users")
    op.drop_table("users")
    op.drop_index("idx_invites_expires", table_name="invites")
    op.drop_index("idx_invites_status", table_name="invites")
    op.drop_table("invites")
