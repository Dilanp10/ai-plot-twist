"""Expand character seed to the full 10-entry roster (R-001 resolved).

Module 013 / Task T-002 follow-up.

Adds the 8 characters confirmed by the PO after research.md R-001 was
closed: merlina, cr7, john-wick, franchella, ibai, putin, dua-lipa,
angelina-jolie.

The INSERT uses ON CONFLICT (slug) DO UPDATE so re-running on an already-
seeded DB is idempotent. Downgrade removes only these 8 rows; the original
2 (messi, bad-bunny) were seeded in 0012 and are left untouched.

Depends on revision 0014 (twists character_id default trigger).
"""

from __future__ import annotations

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

_NEW_SLUGS: tuple[str, ...] = (
    "merlina",
    "cr7",
    "john-wick",
    "franchella",
    "ibai",
    "putin",
    "dua-lipa",
    "angelina-jolie",
)


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO characters
            (slug, display_name, photo_r2_key, aspect_ratio, sort_order)
        VALUES
            ('merlina', 'Merlina Addams', 'static/characters/merlina.webp', '1:1', 30),
            ('cr7', 'Cristiano Ronaldo', 'static/characters/cr7.webp', '1:1', 40),
            ('john-wick', 'John Wick', 'static/characters/john-wick.webp', '1:1', 50),
            ('franchella', 'Guillermo Franchella',
             'static/characters/franchella.webp', '1:1', 60),
            ('ibai', 'Ibai Llanos', 'static/characters/ibai.webp', '1:1', 70),
            ('putin', 'Vladimir Putin', 'static/characters/putin.webp', '1:1', 80),
            ('dua-lipa', 'Dua Lipa', 'static/characters/dua-lipa.webp', '1:1', 90),
            ('angelina-jolie', 'Angelina Jolie',
             'static/characters/angelina-jolie.webp', '1:1', 100)
        ON CONFLICT (slug) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            photo_r2_key = EXCLUDED.photo_r2_key,
            aspect_ratio = EXCLUDED.aspect_ratio,
            sort_order   = EXCLUDED.sort_order,
            updated_at   = now()
        """
    )


def downgrade() -> None:
    slug_list = ", ".join(f"'{s}'" for s in _NEW_SLUGS)
    op.execute(f"DELETE FROM characters WHERE slug IN ({slug_list})")
