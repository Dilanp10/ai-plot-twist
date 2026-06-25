"""characters table for the I2V seed catalog.

Module 013 / Task T-001.

One table holding the fixed roster of characters that users pick when
submitting a twist (module 005 delta). Each row has a slug, display name,
R2 photo key, aspect ratio and an active flag. The endpoint
(``GET /characters``) and the FK from ``twists.character_id`` consume this
table; module 008 reads ``photo_r2_key`` to seed the I2V provider.

Depends on revision 0010 (state_transitions next_chapter_id).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "characters",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("slug", sa.Text, nullable=False, unique=True),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("photo_r2_key", sa.Text, nullable=False),
        sa.Column(
            "aspect_ratio",
            sa.Text,
            nullable=False,
            server_default=sa.text("'1:1'"),
        ),
        sa.Column(
            "active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "sort_order",
            sa.Integer,
            nullable=False,
            server_default=sa.text("100"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9-]{2,40}$'",
            name="ck_characters_slug_format",
        ),
        sa.CheckConstraint(
            "char_length(display_name) BETWEEN 2 AND 60",
            name="ck_characters_display_name_len",
        ),
        sa.CheckConstraint(
            "photo_r2_key LIKE 'static/characters/%.webp'",
            name="ck_characters_photo_r2_key_format",
        ),
        sa.CheckConstraint(
            "aspect_ratio IN ('1:1', '9:16', '16:9')",
            name="ck_characters_aspect_ratio",
        ),
    )
    op.create_index(
        "idx_characters_active_sort",
        "characters",
        ["sort_order", "id"],
        postgresql_where=sa.text("active = TRUE"),
    )


def downgrade() -> None:
    op.drop_index("idx_characters_active_sort", table_name="characters")
    op.drop_table("characters")
