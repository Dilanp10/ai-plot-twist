"""Backfill trigger for ``twists.character_id`` (test compatibility).

Module 005 delta-i2v-character / Task T-017b (test compatibility).

The Ronda 7 pivot made ``character_id`` ``NOT NULL`` on ``twists``
(migration 0013). Legacy unit/integration test setups insert rows via
raw SQL (e.g. ``INSERT INTO twists (chapter_id, user_id, content, status)
VALUES (...)``) without supplying ``character_id``. Refactoring those
~50 sites is out of scope for this PR.

To keep the column ``NOT NULL`` while not breaking the existing test
corpus, this migration installs a ``BEFORE INSERT`` trigger that fills
``character_id`` with the lowest-``sort_order`` active character when
the caller leaves it ``NULL``. Production code paths always pass an
explicit id; the trigger is a no-op for them.

The trigger remains safe even after the test corpus is refactored —
when every caller supplies an explicit id, the trigger body never
executes its ``NEW.character_id := ...`` branch. It can be dropped in
a future cleanup migration without risk.

Depends on revision 0013 (character_id column).
"""

from __future__ import annotations

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION twists_default_character_id()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.character_id IS NULL THEN
                NEW.character_id := (
                    SELECT id FROM characters
                    WHERE active = TRUE
                    ORDER BY sort_order ASC, id ASC
                    LIMIT 1
                );
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_twists_default_character_id
        BEFORE INSERT ON twists
        FOR EACH ROW
        EXECUTE FUNCTION twists_default_character_id();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_twists_default_character_id ON twists")
    op.execute("DROP FUNCTION IF EXISTS twists_default_character_id()")
