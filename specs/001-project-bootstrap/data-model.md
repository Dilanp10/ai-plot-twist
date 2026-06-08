# Data Model: Project Setup and Bootstrap

**Branch**: `001-project-bootstrap` | **Date**: 2026-06-07

This feature introduces **one** table: `idempotency_keys`. No business entities ship in
module 001. The baseline migration (`alembic/versions/0001_baseline.py`) creates this
table and nothing else; future modules each add their own migrations.

The full target schema for the application is documented in [../../SDD.md](../../SDD.md)
§3. Each subsequent module owns a fragment of that schema and ships the corresponding
migration.

---

## Entities introduced in this feature

### `idempotency_keys`

Stores client-supplied idempotency tokens so that any state-mutating endpoint (added in
future modules) can replay safely. Module 001 itself does not yet write to this table —
it ships only the schema, so module 002 (which introduces the first mutating endpoint
behind `/auth/redeem-invite`) can use it without a migration of its own.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `key` | `TEXT` | `PRIMARY KEY` | Client-generated `Idempotency-Key` header value (UUID v4 recommended; ULID accepted). |
| `user_id` | `BIGINT` | `NULLABLE`, no FK in this feature | Will FK to `users.id` once module 002 creates that table; added in a follow-up migration. |
| `request_hash` | `TEXT` | `NOT NULL` | SHA-256 of the normalized request body. Used to reject collisions where the same key reuses with different payloads. |
| `response_json` | `JSONB` | `NOT NULL` | The persisted response. Returned verbatim on replay. |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` | For TTL cleanup. |

**Indexes**:

| Name | Columns | Purpose |
|---|---|---|
| `idx_idem_created` | `(created_at)` | Supports a future `DELETE WHERE created_at < now() - INTERVAL '14 days'` cleanup job (not in this feature). |

**Retention**: not enforced in module 001. A cleanup job will be added with module 002
or later. Estimated row growth in MVP: < 100/day.

---

## Why a separate baseline migration?

Splitting the schema by module (rather than shipping one giant initial migration like
some Spec Kit examples do) keeps the migration history aligned with the spec history.
Each module's `data-model.md` lists exactly what it adds; you can `git blame` an FK to a
PR with no archaeology.

The trade-off is that the early modules have very small migrations; that is acceptable.

---

## Alembic conventions adopted for the project

These are repository-wide conventions to be honored by every future migration:

1. **File name**: `NNNN_<slug>.py`, where `NNNN` is a 4-digit serial.
2. **Down-revision chain**: strictly linear. No branching/merging. The `0001_baseline`
   migration has `down_revision = None`.
3. **`upgrade()` is reversible** unless it deletes data; the matching `downgrade()`
   either reverses the change or raises `NotImplementedError("irreversible")`.
4. **No data migrations in schema migrations**. Backfills live in
   `alembic/data_migrations/NNNN_<slug>.py` and are run by an explicit
   `pnpm data-migrate` script (added when the first backfill is needed).
5. **No `IF NOT EXISTS`**. If a migration is re-run, that's a bug to surface, not hide.
6. **Constraints inline with `CREATE TABLE`** for clarity.
7. **Times are `TIMESTAMPTZ`** always. Never `TIMESTAMP WITHOUT TIME ZONE`.
8. **Identifiers**: `BIGSERIAL` PKs for internal stable IDs, `UUID DEFAULT
   gen_random_uuid()` for any column exposed to clients.

---

## Baseline migration body (reference)

```python
# alembic/versions/0001_baseline.py
"""baseline — idempotency_keys

Revision ID: 0001
Revises:
Create Date: 2026-06-07
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("user_id", sa.BigInteger, nullable=True),
        sa.Column("request_hash", sa.Text, nullable=False),
        sa.Column("response_json", sa.dialects.postgresql.JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_idem_created", "idempotency_keys", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("idx_idem_created", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
```

**Note on `pgcrypto`**: enabled here (not later) so that the first module needing
`gen_random_uuid()` (module 002 for `users.public_id`) does not have to ship an
extension-enabling migration. Idempotent thanks to `IF NOT EXISTS` (the single allowed
use of that clause, scoped to extensions).
