"""Add ``character_id`` FK to ``twists`` (I2V pivot).

Module 005 delta-i2v-character / Task T-017.

Ronda 7 (SDD §A #29, ADR-0008): every twist proposal must point at a fixed
character from the catalog (module 013). The FK is added in three steps so
existing rows survive:

  1. Add the column nullable.
  2. Backfill missing values with the lowest-``sort_order`` active character
     (degraded-but-valid for legacy rows; expected to be a no-op in prod
     where ``twists`` is empty pre-Ronda 7 — see delta-005 R-NEW-1).
  3. Flip the column to ``NOT NULL`` and add the index.

Depends on revision 0012 (characters seed) — the backfill ``SELECT … FROM
characters`` would fail if the catalog table did not exist yet.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "twists",
        sa.Column("character_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_twists_character",
        "twists",
        "characters",
        ["character_id"],
        ["id"],
    )
    # Backfill any legacy rows with the lowest-sort_order active character.
    # If both tables are empty the UPDATE is a no-op.
    op.execute(
        """
        UPDATE twists
        SET character_id = (
            SELECT id FROM characters
            WHERE active = TRUE
            ORDER BY sort_order ASC, id ASC
            LIMIT 1
        )
        WHERE character_id IS NULL
        """
    )
    op.alter_column("twists", "character_id", nullable=False)
    op.create_index("idx_twists_character_id", "twists", ["character_id"])


def downgrade() -> None:
    op.drop_index("idx_twists_character_id", table_name="twists")
    op.drop_constraint("fk_twists_character", "twists", type_="foreignkey")
    op.drop_column("twists", "character_id")
