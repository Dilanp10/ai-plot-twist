# Data Model: Voting

**Branch**: `007-voting` | **Date**: 2026-06-07

One new table: `votes`. Schema mirrors SDD §3.1. One migration: `0008_votes.py`.

---

## Entity

### `votes`

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `BIGSERIAL` | `PRIMARY KEY` | |
| `twist_id` | `BIGINT` | `NOT NULL REFERENCES twists(id) ON DELETE CASCADE` | |
| `user_id` | `BIGINT` | `NOT NULL REFERENCES users(id)` | Author of the vote. |
| `chapter_id` | `BIGINT` | `NOT NULL REFERENCES chapters(id) ON DELETE CASCADE` | Denormalized from `twists.chapter_id` for fast quota count. Service layer enforces equality on insert. CASCADE so cleanup of a chapter takes its votes along with it (votes also CASCADE via `twist_id`, but the redundant CASCADE on `chapter_id` keeps the FK chain consistent if a chapter is ever deleted without first deleting its twists). |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` | |
| (uniq) | | `UNIQUE (twist_id, user_id)` | **One vote per user per twist.** Atomic enforcement. |

**Indexes**:

| Name | Columns | Purpose |
|---|---|---|
| `uniq_votes_twist_user` | `(twist_id, user_id)` UNIQUE | Idempotency anchor. Auto-created by UNIQUE constraint. |
| `idx_votes_twist` | `(twist_id)` | Counting `vote_count` per twist in `vote-feed`. |
| `idx_votes_user_chapter` | `(user_id, chapter_id)` | Per-user-per-chapter quota count and `has_my_vote` lookup. |

**Invariant** (service-layer enforced):
- `votes.chapter_id == twists.chapter_id` where `twists.id = votes.twist_id`. Not a
  DB CHECK because cross-table CHECKs are awkward in PG; instead the service does a
  read of `twists.chapter_id` and writes both columns with the verified value.

---

## Migration

### `0008_votes.py`

```python
"""votes"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"

def upgrade():
    op.create_table(
        "votes",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("twist_id", sa.BigInteger,
                  sa.ForeignKey("twists.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("user_id", sa.BigInteger,
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("chapter_id", sa.BigInteger,
                  sa.ForeignKey("chapters.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("twist_id", "user_id",
                            name="uniq_votes_twist_user"),
    )
    op.create_index("idx_votes_twist", "votes", ["twist_id"])
    op.create_index("idx_votes_user_chapter", "votes",
                    ["user_id", "chapter_id"])


def downgrade():
    op.drop_index("idx_votes_user_chapter", table_name="votes")
    op.drop_index("idx_votes_twist", table_name="votes")
    op.drop_table("votes")
```

---

## Vote-feed read query

```sql
-- 1. Approved twists with current vote_count
SELECT t.id, t.public_id, t.content,
       COUNT(v.id) AS vote_count,
       t.submitted_at
  FROM twists t
  LEFT JOIN votes v ON v.twist_id = t.id
 WHERE t.chapter_id = :chapter_id
   AND t.status = 'approved'
 GROUP BY t.id;

-- 2. Current user's voted twist ids (for has_my_vote)
SELECT twist_id FROM votes
 WHERE user_id = :user_id AND chapter_id = :chapter_id;
```

Both queries hit `idx_twists_chapter_status` and `idx_votes_user_chapter`
respectively. Combined Python work shuffles by seed (R-001), applies cursor offset,
projects `has_my_vote` from the set lookup.

**Expected p95**: ~30 ms server-side at 100 approved twists.

---

## Vote-cast transaction (SQL outline)

```sql
BEGIN;
SET LOCAL lock_timeout = '1000ms';

-- 1. Resolve twist; verify it's approved + belongs to live chapter
SELECT t.id, t.chapter_id, t.user_id AS owner_id, t.status
  FROM twists t
  JOIN chapters c ON c.id = t.chapter_id
 WHERE t.public_id = :twist_public_id
   AND c.status = 'live';
-- IF none / status != 'approved': ROLLBACK; 409 twist_not_votable.

-- 2. Self-vote gate (if disabled)
-- IF owner_id == :user_id AND NOT ALLOW_SELF_VOTE: ROLLBACK; 409 cannot_self_vote.

-- 3. Quota lock
SELECT pg_advisory_xact_lock(
    hashtext('vote_quota:' || :user_id::text || ':' || :chapter_id::text)
);

-- 4. Count under lock
SELECT COUNT(*) FROM votes
 WHERE user_id = :user_id AND chapter_id = :chapter_id;
-- IF count >= MAX_VOTES_PER_USER_PER_CHAPTER: ROLLBACK; 409 over_quota.

-- 5. Atomic insert
INSERT INTO votes (twist_id, user_id, chapter_id)
VALUES (:twist_id, :user_id, :chapter_id)
ON CONFLICT (twist_id, user_id) DO NOTHING
RETURNING id;
-- IF 0 rows returned: ROLLBACK; 409 already_voted.

-- 6. New vote_count
SELECT COUNT(*) FROM votes WHERE twist_id = :twist_id;

COMMIT;
```

**Note on step 5**: the `ON CONFLICT DO NOTHING` is what makes the operation
naturally idempotent. A double-tap on the same twist by the same user runs the
insert twice; the second insert affects 0 rows and the service returns 409
`already_voted`. No deadlock, no double-count.

---

## What this module does NOT touch

- `twists.status` (read-only). The filter (006) sets it; voting only reads
  `'approved'`.
- `cycles.state` (read-only via service from module 003).
- `state_transitions` (untouched).
- `chapters` (read for the live-chapter resolve).
