"""votes table for user-cast votes on approved twists.

Module 007 / Task T-001.

One row per (user, twist). The UNIQUE constraint on (twist_id, user_id)
is the idempotency anchor: vote-cast uses ``ON CONFLICT DO NOTHING`` so a
double-tap returns 0 rows and the service maps it to 409 ``already_voted``.

``chapter_id`` is denormalized from ``twists.chapter_id`` to make the
per-user-per-chapter quota count + ``has_my_vote`` lookup hit a single
narrow index. The service layer enforces the invariant
``votes.chapter_id == twists.chapter_id`` on every insert.

Depends on revision 0007 (twists).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "votes",
        sa.Column(
            "id",
            sa.BigInteger,
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column(
            "twist_id",
            sa.BigInteger,
            sa.ForeignKey("twists.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "chapter_id",
            sa.BigInteger,
            sa.ForeignKey("chapters.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "twist_id",
            "user_id",
            name="uniq_votes_twist_user",
        ),
    )
    op.create_index("idx_votes_twist", "votes", ["twist_id"])
    op.create_index(
        "idx_votes_user_chapter",
        "votes",
        ["user_id", "chapter_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_votes_user_chapter", table_name="votes")
    op.drop_index("idx_votes_twist", table_name="votes")
    op.drop_table("votes")
