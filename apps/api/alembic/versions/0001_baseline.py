"""baseline — idempotency_keys

Creates the only table introduced by module 001:

  ``idempotency_keys`` — stores client ``Idempotency-Key`` tokens so any future
  state-mutating endpoint can replay safely (constitution Gate 2). Module 001
  itself does not write to this table; module 002 is the first writer when it
  ships ``/auth/redeem-invite``.

The ``pgcrypto`` extension is enabled here so future modules can call
``gen_random_uuid()`` without shipping an extension-enabling migration of
their own.

Revision ID: 0001
Revises:
Create Date: 2026-06-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create idempotency_keys + enable pgcrypto."""
    # IF NOT EXISTS is allowed here because extensions are cluster-level and
    # may already be enabled by a previous deploy or by a sibling DB on the
    # same Postgres cluster (data-model.md §"Note on pgcrypto").
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.Text, primary_key=True),
        # FK to users.id is intentionally deferred to a follow-up migration in
        # module 002, which is the module that creates the users table.
        sa.Column("user_id", sa.BigInteger, nullable=True),
        sa.Column("request_hash", sa.Text, nullable=False),
        sa.Column("response_json", postgresql.JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # Supports the future cleanup job:
    #   DELETE FROM idempotency_keys WHERE created_at < now() - INTERVAL '14 days'
    op.create_index("idx_idem_created", "idempotency_keys", ["created_at"])


def downgrade() -> None:
    """Drop idempotency_keys. pgcrypto is left enabled (cluster-level)."""
    op.drop_index("idx_idem_created", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
