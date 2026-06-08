# Data Model: Twist Submission

**Branch**: `005-twists-submission` | **Date**: 2026-06-07

One new table: `twists`. Schema mirrors SDD §3.1 (refined by Ronda 1 to include the
`deleted_by_user` status and `deleted_at` column). One migration: `0007_twists.py`.

---

## Entity

### `twists`

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `BIGSERIAL` | `PRIMARY KEY` | Internal id. |
| `public_id` | `UUID` | `NOT NULL UNIQUE DEFAULT gen_random_uuid()` | Exposed to clients. |
| `chapter_id` | `BIGINT` | `NOT NULL REFERENCES chapters(id) ON DELETE CASCADE` | The chapter this twist proposes a continuation for. |
| `user_id` | `BIGINT` | `NOT NULL REFERENCES users(id)` | Author. |
| `content` | `TEXT` | `NOT NULL CHECK (char_length(content) BETWEEN 5 AND 280)` | NFKC-normalized before insert. |
| `status` | `TEXT` | `NOT NULL CHECK (status IN ('pending_review','approved','rejected_offensive','rejected_incoherent','rejected_spam','deleted_by_user'))` | Lifecycle. |
| `director_reason` | `TEXT` | `NULLABLE` | Brief LLM justification (set by module 006). |
| `submitted_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` | |
| `reviewed_at` | `TIMESTAMPTZ` | `NULLABLE` | Set by module 006 when filter runs. |
| `deleted_at` | `TIMESTAMPTZ` | `NULLABLE` | Set by this module when user soft-deletes. |

**Indexes**:

| Name | Columns | Purpose |
|---|---|---|
| `idx_twists_chapter_status` | `(chapter_id, status)` | Used by module 006 (filter) and module 007 (vote-feed) — listed here so module 005 ships it. |
| `idx_twists_user_chapter` | `(user_id, chapter_id)` | Used by quota count (FR-004) and `GET /me/twists` (FR-008). **Critical** for hot path. |

**Constraints**:

- Status transitions are enforced by application logic, not DB. (A future DB trigger
  could enforce, but adds friction for now.)
- The `(status='deleted_by_user') = (deleted_at IS NOT NULL)` invariant is enforced
  by a CHECK constraint:

  ```sql
  CHECK ((status = 'deleted_by_user') = (deleted_at IS NOT NULL))
  ```

---

## Migration

### `0007_twists.py`

```python
"""twists"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0007"
down_revision = "0006"

def upgrade():
    op.create_table(
        "twists",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("public_id", UUID(as_uuid=True), nullable=False, unique=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("chapter_id", sa.BigInteger,
                  sa.ForeignKey("chapters.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("user_id", sa.BigInteger,
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False,
                  server_default=sa.text("'pending_review'")),
        sa.Column("director_reason", sa.Text, nullable=True),
        sa.Column("submitted_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "char_length(content) BETWEEN 5 AND 280",
            name="ck_twists_content_len",
        ),
        sa.CheckConstraint(
            "status IN ('pending_review','approved','rejected_offensive',"
            "'rejected_incoherent','rejected_spam','deleted_by_user')",
            name="ck_twists_status",
        ),
        sa.CheckConstraint(
            "(status = 'deleted_by_user') = (deleted_at IS NOT NULL)",
            name="ck_twists_deleted_consistency",
        ),
    )
    op.create_index("idx_twists_chapter_status", "twists",
                    ["chapter_id", "status"])
    op.create_index("idx_twists_user_chapter", "twists",
                    ["user_id", "chapter_id"])


def downgrade():
    op.drop_index("idx_twists_user_chapter", table_name="twists")
    op.drop_index("idx_twists_chapter_status", table_name="twists")
    op.drop_table("twists")
```

---

## Submission transaction (SQL outline)

The body of `TwistSubmissionService.submit(user_id, chapter_public_id, content,
idempotency_key)` runs:

```sql
BEGIN;
SET LOCAL lock_timeout = '1000ms';

-- 1. Kill-switch & cycle state checks (out of band, no DB write yet).

-- 2. Idempotency-Key replay check (no lock yet)
SELECT response_json FROM idempotency_keys
 WHERE key = :idem_key
   FOR SHARE;
-- IF found AND request_hash matches: ROLLBACK; return cached 200.
-- IF found AND request_hash differs: ROLLBACK; return 409 idempotency_conflict.

-- 3. Resolve chapter
SELECT id FROM chapters WHERE public_id = :chapter_public_id AND status = 'live';
-- IF none: ROLLBACK; return 409 chapter_mismatch.

-- 4. Acquire per-user-per-chapter lock
SELECT pg_advisory_xact_lock(
    hashtext('twist_quota:' || :user_id::text || ':' || :chapter_id::text)
);

-- 5. Re-read quota under lock
SELECT COUNT(*) FROM twists
 WHERE user_id = :user_id AND chapter_id = :chapter_id;
-- IF count >= MAX_TWISTS_PER_USER_PER_CHAPTER: ROLLBACK; return 409 over_quota.

-- 6. Insert
INSERT INTO twists (chapter_id, user_id, content, status)
VALUES (:chapter_id, :user_id, :normalized_content, 'pending_review')
RETURNING id, public_id, submitted_at;

-- 7. Persist idempotency entry
INSERT INTO idempotency_keys (key, user_id, request_hash, response_json)
VALUES (:idem_key, :user_id, :body_hash, :response_payload);

COMMIT;
```

The combination of `pg_advisory_xact_lock` + recount-under-lock ensures the quota
is honored even under concurrent submits.

---

## Delete transaction (SQL outline)

```sql
BEGIN;
-- 1. Lock the twist row (cheap; PK index)
SELECT id, status, deleted_at, user_id
  FROM twists
 WHERE public_id = :twist_public_id
   FOR UPDATE;
-- IF not found: ROLLBACK; 404 twist_not_found.
-- IF user_id != :auth_user_id: ROLLBACK; 403 forbidden_not_owner.
-- IF status NOT IN ('pending_review','deleted_by_user'): ROLLBACK; 409 already_filtered.

-- 2. Idempotent: if already deleted, return existing deleted_at without changing.
-- IF status = 'deleted_by_user': COMMIT; return 200 with original deleted_at.

-- 3. Soft delete
UPDATE twists
   SET status = 'deleted_by_user', deleted_at = now()
 WHERE id = :twist_id
RETURNING deleted_at;

COMMIT;
```

No advisory lock on DELETE — `SELECT … FOR UPDATE` on a single row by PK is enough
serialization. Quota count is unaffected by FR-004.

---

## `GET /me/twists` read query

```sql
SELECT public_id, content, status, director_reason,
       submitted_at, deleted_at
  FROM twists
 WHERE user_id = :user_id
   AND chapter_id = :chapter_id
 ORDER BY submitted_at ASC
 LIMIT :limit;
```

Limit is `MAX_TWISTS_PER_USER_PER_CHAPTER + 1` (defensive; we never expect more
rows). Uses `idx_twists_user_chapter`. Quota fields are derived in Python from the
same result set (`used = len(items)`, `remaining = max(0, MAX - used)`).
