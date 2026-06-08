# Data Model: Web Push

**Branch**: `011-web-push` | **Date**: 2026-06-07

One new table: `push_subscriptions`. Schema mirrors SDD §3.1 (added in
Ronda 1). One migration: `0009_push_subscriptions.py`. No other schema
changes.

---

## Entity

### `push_subscriptions`

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `BIGSERIAL` | `PRIMARY KEY` | Internal id; exposed in URL for DELETE. |
| `user_id` | `BIGINT` | `NOT NULL REFERENCES users(id) ON DELETE CASCADE` | Owner. |
| `endpoint` | `TEXT` | `NOT NULL UNIQUE` | The push service URL (FCM, autopush, APN). Unique per browser/profile. |
| `p256dh_key` | `TEXT` | `NOT NULL` | The user's public key (base64-url). |
| `auth_key` | `TEXT` | `NOT NULL` | The user's auth secret (base64-url). |
| `user_agent` | `TEXT` | `NULLABLE` | For diagnostics ("which browser failed?"). |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` | |
| `last_success_at` | `TIMESTAMPTZ` | `NULLABLE` | Last successful push to this endpoint. |
| `failure_count` | `INT` | `NOT NULL DEFAULT 0 CHECK (failure_count >= 0)` | Resets to 0 on success. |

**Indexes**:

| Name | Columns | Purpose |
|---|---|---|
| `uniq_push_endpoint` | `(endpoint)` | UNIQUE; UPSERT anchor for re-subscribe. |
| `idx_push_user` | `(user_id)` | Fan-out joins; "all subs for user X". |

**Deletion semantics**: HARD DELETE (per research R-002, constitutional Gate 7
exception documented in ADR-0007).

---

## Migration

### `0009_push_subscriptions.py`

```python
"""push_subscriptions"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"   # follows votes (module 007)

def upgrade():
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger,
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("endpoint", sa.Text, nullable=False, unique=True),
        sa.Column("p256dh_key", sa.Text, nullable=False),
        sa.Column("auth_key", sa.Text, nullable=False),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("last_success_at", sa.TIMESTAMP(timezone=True),
                  nullable=True),
        sa.Column("failure_count", sa.Integer, nullable=False,
                  server_default=sa.text("0")),
        sa.CheckConstraint("failure_count >= 0",
                           name="ck_push_failure_count_nonneg"),
    )
    op.create_index("idx_push_user", "push_subscriptions", ["user_id"])


def downgrade():
    op.drop_index("idx_push_user", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
```

---

## Subscribe UPSERT

```sql
INSERT INTO push_subscriptions
  (user_id, endpoint, p256dh_key, auth_key, user_agent)
VALUES (:user_id, :endpoint, :p256dh, :auth, :ua)
ON CONFLICT (endpoint) DO UPDATE
  SET user_id      = EXCLUDED.user_id,
      p256dh_key   = EXCLUDED.p256dh_key,
      auth_key     = EXCLUDED.auth_key,
      user_agent   = EXCLUDED.user_agent,
      failure_count = 0     -- reset on re-subscribe
RETURNING id;
```

**Why `ON CONFLICT (endpoint)`**: if the same browser re-subscribes (e.g., after
clearing site data), the same endpoint comes back; we re-bind it to the current
user (which may differ if the previous owner signed out and a new user signed
up on the same browser).

---

## Fan-out read query

```sql
SELECT ps.id, ps.endpoint, ps.p256dh_key, ps.auth_key, ps.failure_count, ps.last_success_at
  FROM push_subscriptions ps
  JOIN users u ON u.id = ps.user_id
 WHERE u.is_banned = FALSE;
```

Single query, indexed by `users.is_banned` partial index from module 002. At
MVP scale, returns ≤ 100 rows.

---

## Idempotency key (reusing module 001 table)

The fan-out writes one `idempotency_keys` row at the start:

```sql
INSERT INTO idempotency_keys (key, user_id, request_hash, response_json)
VALUES (
  'push_fanout:' || :chapter_public_id,
  NULL,                                       -- not user-scoped
  '',                                         -- no body hash
  '{"started_at": "...", "chapter_id": ...}'::jsonb
)
ON CONFLICT (key) DO NOTHING
RETURNING key;
```

If 0 rows returned → fan-out already ran for this chapter → short-circuit with
`{sent: 0, skipped_idempotent: true}`.

`idempotency_keys.user_id` is NULLABLE in module 001's schema (FK was deferred
to a follow-up migration). The push fan-out is system-scoped, not user-scoped,
so NULL is correct.

---

## Cleanup query (end of fan-out)

```sql
DELETE FROM push_subscriptions
 WHERE id = ANY(:gone_ids);                  -- 410 Gone — deleted above already

DELETE FROM push_subscriptions
 WHERE failure_count >= :threshold
   AND (last_success_at IS NULL
        OR last_success_at < now() - INTERVAL '7 days');
```

Two-pass: explicit gone-list (already gathered) + threshold-based.

---

## What this module does NOT touch

- Any other table.
- The cycle FSM state machine (modules 003).
- Asset URLs (modules 004 / 008).
- The `users` table (read-only).
