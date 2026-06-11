"""twists table for user-submitted plot twists.

Module 005 / Task T-001.

One table holding each twist a user submits during the RECEPCION_IDEAS
window. Quota, idempotency, and lifecycle transitions are enforced by
application logic; this migration only ships schema + CHECK invariants
and the two indices required by the hot paths (per-user quota count and
chapter-wide filter/voting reads).

Depends on revision 0006 (system_flags).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "twists",
        sa.Column(
            "id",
            sa.BigInteger,
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "public_id",
            UUID(as_uuid=True),
            nullable=False,
            unique=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "chapter_id",
            sa.BigInteger,
            sa.ForeignKey("chapters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "status",
            sa.Text,
            nullable=False,
            server_default=sa.text("'pending_review'"),
        ),
        sa.Column("director_reason", sa.Text, nullable=True),
        sa.Column(
            "submitted_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "reviewed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "deleted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "char_length(content) BETWEEN 5 AND 280",
            name="ck_twists_content_len",
        ),
        sa.CheckConstraint(
            "status IN ('pending_review','approved','rejected_offensive',"
            "'rejected_incoherent','rejected_spam','deleted_by_user')",
            name="ck_twists_status",
        ),
        sa.CheckConstraint(
            "(status = 'deleted_by_user') = (deleted_at IS NOT NULL)",
            name="ck_twists_deleted_consistency",
        ),
    )
    op.create_index(
        "idx_twists_chapter_status",
        "twists",
        ["chapter_id", "status"],
    )
    op.create_index(
        "idx_twists_user_chapter",
        "twists",
        ["user_id", "chapter_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_twists_user_chapter", table_name="twists")
    op.drop_index("idx_twists_chapter_status", table_name="twists")
    op.drop_table("twists")
