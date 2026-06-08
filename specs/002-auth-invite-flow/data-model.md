# Data Model: Auth via Invite Code + Device-Bound JWT

**Branch**: `002-auth-invite-flow` | **Date**: 2026-06-07

This feature introduces **three** tables: `users`, `invites`, `rate_limit_buckets`. The
`users` table mirrors the SDD §3.1 definition with one refinement: `invite_code` is a
foreign key to `invites.code` (the SDD originally had it as TEXT + index — this is a
deliberate tightening, audited in [research.md](./research.md#open-items).

Two Alembic migrations ship:

- `0002_users_invites.py` — creates `users` and `invites`. Both in one migration
  because `users.invite_code` FKs `invites.code`.
- `0003_rate_limit_buckets.py` — separate because it's a different concern and may
  evolve independently.

---

## Entities

### `invites`

| Column | Type | Constraints | Description |
|---|---|---|---|
| `code` | `TEXT` | `PRIMARY KEY`, `CHECK (code ~ '^[A-Z2-7]{4}-[A-Z2-7]{4}$')` | Canonicalized code, format `XXXX-XXXX`. |
| `issued_by` | `TEXT` | `NOT NULL` | Free-text label of the issuer ("po", "admin:juan"). |
| `issued_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` | When the code was minted. |
| `expires_at` | `TIMESTAMPTZ` | `NOT NULL` | After which the code is no longer redeemable. |
| `status` | `TEXT` | `NOT NULL CHECK (status IN ('unused','redeemed','revoked','expired'))` | Lifecycle. `expired` is set lazily by `list-invites` or a future cron sweep. |
| `redeemed_at` | `TIMESTAMPTZ` | `NULLABLE` | Set when `status` transitions to `redeemed`. |
| `redeemed_by_user` | `BIGINT` | `NULLABLE, REFERENCES users(id) ON DELETE SET NULL` | Audit pointer to the user who redeemed. |
| `note` | `TEXT` | `NULLABLE` | Optional human note ("para Lucía"). |

**Indexes**:

| Name | Columns | Purpose |
|---|---|---|
| `idx_invites_status` | `(status)` | Used by `list-invites` filters. |
| `idx_invites_expires` | `(expires_at)` WHERE `status = 'unused'` | Used by future expiry sweep. |

**Invariants**:

- A row with `status='redeemed'` MUST have `redeemed_at NOT NULL` and `redeemed_by_user
  NOT NULL`.
- A row with `status='unused'` MUST have `redeemed_at IS NULL`.
- These invariants are enforced by a `CHECK` constraint:
  `CHECK ((status = 'redeemed') = (redeemed_at IS NOT NULL))`.

### `users`

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `BIGSERIAL` | `PRIMARY KEY` | Internal stable ID. |
| `public_id` | `UUID` | `NOT NULL UNIQUE DEFAULT gen_random_uuid()` | Exposed to clients in JWT `sub`. |
| `display_name` | `TEXT` | `NOT NULL CHECK (char_length(display_name) BETWEEN 2 AND 24)` | NFKC-normalized server-side before insert. |
| `invite_code` | `TEXT` | `NOT NULL REFERENCES invites(code)` | The code redeemed by this user. |
| `device_token` | `TEXT` | `NOT NULL UNIQUE CHECK (char_length(device_token) = 64)` | SHA-256 hex of the server-issued device_secret. |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` | |
| `last_seen_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` | Touched on every authenticated request. |
| `is_banned` | `BOOLEAN` | `NOT NULL DEFAULT FALSE` | Set by admin script (out of scope for this module; flag column ships ready). |

**Indexes**:

| Name | Columns | Purpose |
|---|---|---|
| `idx_users_invite_code` | `(invite_code)` | Audit lookup ("who redeemed XYZ?"). |
| `idx_users_last_seen` | `(last_seen_at DESC)` WHERE `is_banned = FALSE` | DAU queries in future analytics. |
| (implicit) | `device_token` UNIQUE | Enforces single-device-per-secret. |

**Invariants**:

- `invite_code` MUST reference an `invites` row whose `status='redeemed'` and
  `redeemed_by_user = users.id`. The redemption transaction sets both atomically.
- `device_token` MUST be exactly 64 hex chars (SHA-256 output). CHECK constraint
  enforces.

### `rate_limit_buckets`

| Column | Type | Constraints | Description |
|---|---|---|---|
| `bucket_key` | `TEXT` | `NOT NULL` | e.g. `redeem:ip:1.2.3.4`. |
| `window_start` | `TIMESTAMPTZ` | `NOT NULL` | `date_trunc('hour', now())` at insert time. |
| `count` | `INTEGER` | `NOT NULL DEFAULT 1 CHECK (count >= 0)` | |
| (PK) | | `PRIMARY KEY (bucket_key, window_start)` | |

**Retention**: cleanup deletes rows where `window_start < now() - INTERVAL '7 days'`.
The cleanup job is added with module 003 (watchdog runs once a day); until then, the
table is tiny in closed beta.

---

## Migrations

### `0002_users_invites.py`

```python
"""users + invites"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invites",
        sa.Column("code", sa.Text, primary_key=True),
        sa.Column("issued_by", sa.Text, nullable=False),
        sa.Column("issued_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("redeemed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("redeemed_by_user", sa.BigInteger, nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.CheckConstraint(
            "code ~ '^[A-Z2-7]{4}-[A-Z2-7]{4}$'", name="ck_invites_code_format"
        ),
        sa.CheckConstraint(
            "status IN ('unused','redeemed','revoked','expired')",
            name="ck_invites_status",
        ),
        sa.CheckConstraint(
            "(status = 'redeemed') = (redeemed_at IS NOT NULL)",
            name="ck_invites_redeemed_consistency",
        ),
    )
    op.create_index("idx_invites_status", "invites", ["status"])
    op.create_index(
        "idx_invites_expires",
        "invites",
        ["expires_at"],
        postgresql_where=sa.text("status = 'unused'"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("public_id", UUID(as_uuid=True), nullable=False, unique=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("invite_code", sa.Text, sa.ForeignKey("invites.code"),
                  nullable=False),
        sa.Column("device_token", sa.Text, nullable=False, unique=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("is_banned", sa.Boolean, nullable=False,
                  server_default=sa.text("FALSE")),
        sa.CheckConstraint(
            "char_length(display_name) BETWEEN 2 AND 24",
            name="ck_users_display_name_len",
        ),
        sa.CheckConstraint(
            "char_length(device_token) = 64",
            name="ck_users_device_token_len",
        ),
    )
    op.create_index("idx_users_invite_code", "users", ["invite_code"])
    op.create_index(
        "idx_users_last_seen",
        "users",
        [sa.text("last_seen_at DESC")],
        postgresql_where=sa.text("is_banned = FALSE"),
    )

    # Backfill the FK pointer on invites → users now that users exists.
    op.create_foreign_key(
        "fk_invites_redeemed_by_user",
        "invites", "users",
        ["redeemed_by_user"], ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_invites_redeemed_by_user", "invites", type_="foreignkey")
    op.drop_index("idx_users_last_seen", table_name="users")
    op.drop_index("idx_users_invite_code", table_name="users")
    op.drop_table("users")
    op.drop_index("idx_invites_expires", table_name="invites")
    op.drop_index("idx_invites_status", table_name="invites")
    op.drop_table("invites")
```

### `0003_rate_limit_buckets.py`

```python
"""rate_limit_buckets"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_buckets",
        sa.Column("bucket_key", sa.Text, nullable=False),
        sa.Column("window_start", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("count", sa.Integer, nullable=False,
                  server_default=sa.text("1")),
        sa.PrimaryKeyConstraint("bucket_key", "window_start"),
        sa.CheckConstraint("count >= 0", name="ck_rate_limit_count_nonneg"),
    )


def downgrade() -> None:
    op.drop_table("rate_limit_buckets")
```

---

## Redemption transaction (SQL outline)

The full body is implemented in `apps/api/app/api/auth.py` and goes through SQLAlchemy
ORM, but the SQL pattern is:

```sql
BEGIN;

-- 1. Rate-limit check (before any code lookup)
INSERT INTO rate_limit_buckets (bucket_key, window_start, count)
VALUES (:bucket, date_trunc('hour', now()), 1)
ON CONFLICT (bucket_key, window_start)
DO UPDATE SET count = rate_limit_buckets.count + 1
RETURNING count;
-- IF count > 5: ROLLBACK; return 429.

-- 2. Lock the invite row
SELECT code, status, expires_at FROM invites
 WHERE code = :code
 FOR UPDATE;
-- IF none / status != 'unused' / expires_at < now(): ROLLBACK; return 404.

-- 3. Insert the user
INSERT INTO users (display_name, invite_code, device_token)
VALUES (:display_name, :code, :device_token_hash)
RETURNING id, public_id, created_at;

-- 4. Mark the invite as redeemed
UPDATE invites
   SET status = 'redeemed',
       redeemed_at = now(),
       redeemed_by_user = :new_user_id
 WHERE code = :code;

COMMIT;
```

The `FOR UPDATE` lock in step 2 serializes concurrent redemptions of the same code;
exactly one transaction commits.
