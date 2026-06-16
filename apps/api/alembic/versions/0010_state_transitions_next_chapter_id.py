"""Ensure state_transitions.next_chapter_id exists.

Module 008 follow-up.

The generation pipeline (``app.domain.generation_pipeline``) inserts the
``GENERACION → PENDING_RELEASE`` transition with a ``next_chapter_id``
column. Migration 0005 was extended to declare this column, but databases
that applied 0005 *before* that edit are missing it. This migration adds
it idempotently (``IF NOT EXISTS``) so both already-migrated and fresh
databases converge on the same schema.

Depends on revision 0009 (push_subscriptions).
"""

from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE state_transitions "
        "ADD COLUMN IF NOT EXISTS next_chapter_id BIGINT "
        "REFERENCES chapters(id)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE state_transitions DROP COLUMN IF EXISTS next_chapter_id")
