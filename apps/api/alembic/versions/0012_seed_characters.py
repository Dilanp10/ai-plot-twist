"""Seed the characters catalog with PO-confirmed roster.

Module 013 / Task T-002.

Idempotent seed for the I2V character catalog. Ships with the 2 PO-confirmed
rows (Messi, Bad Bunny). The remaining 8-12 entries are added by a follow-up
migration once research.md R-001 is closed by the PO. With 2 rows the
endpoint is enough to unblock the module 005 delta development.

The seed uses ``INSERT … ON CONFLICT (slug) DO UPDATE`` so re-running on a
partially-seeded DB converges instead of failing.

Depends on revision 0011 (characters table).
"""

from __future__ import annotations

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


_SEED_SLUGS: tuple[str, ...] = ("messi", "bad-bunny")


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO characters
            (slug, display_name, photo_r2_key, aspect_ratio, sort_order)
        VALUES
            ('messi',     'Lionel Messi', 'static/characters/messi.webp',     '1:1', 10),
            ('bad-bunny', 'Bad Bunny',    'static/characters/bad-bunny.webp', '1:1', 20)
        ON CONFLICT (slug) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            photo_r2_key = EXCLUDED.photo_r2_key,
            aspect_ratio = EXCLUDED.aspect_ratio,
            sort_order   = EXCLUDED.sort_order,
            updated_at   = now()
        """
    )


def downgrade() -> None:
    slug_list = ", ".join(f"'{s}'" for s in _SEED_SLUGS)
    op.execute(f"DELETE FROM characters WHERE slug IN ({slug_list})")
