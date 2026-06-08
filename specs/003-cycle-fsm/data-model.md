# Data Model: Daily Cycle FSM

**Branch**: `003-cycle-fsm` | **Date**: 2026-06-07

This feature introduces **five** tables: `seasons`, `chapters`, `cycles`,
`state_transitions`, `system_flags`. Three Alembic migrations ship:

- `0004_seasons_chapters.py` — `seasons` + `chapters` (referenced by `cycles`).
- `0005_cycles_transitions.py` — `cycles` + `state_transitions`.
- `0006_system_flags.py` — `system_flags` (kill-switch et al.).

---

## Entities

### `seasons`

Mirrors SDD §3.1. One active season at a time, enforced by partial unique index.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `BIGSERIAL` | `PRIMARY KEY` | |
| `slug` | `TEXT` | `NOT NULL UNIQUE` | e.g. `s01-el-tunel`. |
| `title` | `TEXT` | `NOT NULL` | |
| `bible_json` | `JSONB` | `NOT NULL` | World rules, characters, tone. |
| `started_on` | `DATE` | `NOT NULL` | First chapter's release date. |
| `ended_on` | `DATE` | `NULLABLE` | Set when the season closes. |
| `is_active` | `BOOLEAN` | `NOT NULL DEFAULT TRUE` | |

**Index**: `uniq_one_active_season ON seasons(is_active) WHERE is_active = TRUE`.

### `chapters`

Mirrors SDD §3.1. Owned by module 003 in MVP for stable schema; module 004 builds
the read API on top.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `BIGSERIAL` | `PRIMARY KEY` | |
| `public_id` | `UUID` | `NOT NULL UNIQUE DEFAULT gen_random_uuid()` | Exposed to clients. |
| `season_id` | `BIGINT` | `NOT NULL REFERENCES seasons(id) ON DELETE CASCADE` | |
| `day_index` | `INT` | `NOT NULL` | 1, 2, 3… |
| `title` | `TEXT` | `NOT NULL` | |
| `synopsis` | `TEXT` | `NOT NULL` | |
| `manifest_json` | `JSONB` | `NOT NULL` | `{panels:[…], cliffhanger, next_cliffhanger_seed}`. |
| `status` | `TEXT` | `NOT NULL CHECK (status IN ('draft','generating','ready','ready_degraded','live','archived'))` | |
| `released_at` | `TIMESTAMPTZ` | `NULLABLE` | NULL until `live`. |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` | |
| (uniq) | | `UNIQUE (season_id, day_index)` | |

**Index**: `idx_chapters_status_release ON chapters(status, released_at)`.

### `cycles`

The daily state. One row per (season, calendar day in ART).

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `BIGSERIAL` | `PRIMARY KEY` | |
| `season_id` | `BIGINT` | `NOT NULL REFERENCES seasons(id)` | |
| `chapter_id` | `BIGINT` | `NOT NULL REFERENCES chapters(id)` | The chapter on screen today. |
| `next_chapter_id` | `BIGINT` | `NULLABLE REFERENCES chapters(id)` | The chapter being generated for tomorrow. NULL until the pipeline writes it. |
| `state` | `TEXT` | `NOT NULL CHECK (state IN ('PENDING_RELEASE','ESTRENO','RECEPCION_IDEAS','FILTERING','VOTACION','GENERACION','FAILED'))` | Note: Spanish state names — see research R-009. |
| `state_entered_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` | Used by min-dwell checks and watchdog. |
| `cycle_date` | `DATE` | `NOT NULL` | The ART calendar date this cycle anchors to. |
| (uniq) | | `UNIQUE (season_id, cycle_date)` | One cycle per day per season. |

**Index**: `idx_cycles_state ON cycles(state)` — used by `health/cycle` and watchdog.

### `state_transitions`

Append-only audit + idempotency table.

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `BIGSERIAL` | `PRIMARY KEY` | |
| `cycle_id` | `BIGINT` | `NOT NULL REFERENCES cycles(id) ON DELETE CASCADE` | |
| `from_state` | `TEXT` | `NOT NULL` | |
| `to_state` | `TEXT` | `NOT NULL` | |
| `triggered_by` | `TEXT` | `NOT NULL CHECK (triggered_by IN ('cron','admin','retry','side_effect','watchdog'))` | |
| `trigger_id` | `TEXT` | `NULLABLE` | GH Actions run-id, local-replay-<uuid>, side-effect-<uuid>. |
| `payload_json` | `JSONB` | `NULLABLE` | Free-form context: error_hash, retry_attempt, etc. |
| `created_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` | |

**Indexes**:

| Name | Columns | Purpose |
|---|---|---|
| `idx_st_cycle_recent` | `(cycle_id, created_at DESC)` | `health/cycle` reads last 5. |
| `uniq_st_trigger` | `(cycle_id, to_state, trigger_id)` UNIQUE WHERE `trigger_id IS NOT NULL` | **Idempotency anchor.** Duplicate insert → caught and translated to 200 `already_applied`. |

### `system_flags`

Single-row table by convention; key/value for ops toggles.

| Column | Type | Constraints |
|---|---|---|
| `flag_key` | `TEXT` | `PRIMARY KEY` |
| `flag_value` | `JSONB` | `NOT NULL` |
| `updated_by` | `TEXT` | `NOT NULL` |
| `updated_at` | `TIMESTAMPTZ` | `NOT NULL DEFAULT now()` |

**Known keys** (MVP):

- `kill_switch` → `{"on": false, "reason": null, "set_at": "<ts>"}`.

In-process cache TTL: 30 s. Cache invalidation explicit on `kill-switch` write.

---

## Migrations

### `0004_seasons_chapters.py`

```python
"""seasons + chapters"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0004"
down_revision = "0003"

def upgrade():
    op.create_table(
        "seasons",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("slug", sa.Text, nullable=False, unique=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("bible_json", JSONB, nullable=False),
        sa.Column("started_on", sa.Date, nullable=False),
        sa.Column("ended_on", sa.Date, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False,
                  server_default=sa.text("TRUE")),
    )
    op.execute(
        "CREATE UNIQUE INDEX uniq_one_active_season "
        "ON seasons(is_active) WHERE is_active = TRUE"
    )

    op.create_table(
        "chapters",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("public_id", UUID(as_uuid=True), nullable=False, unique=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("season_id", sa.BigInteger,
                  sa.ForeignKey("seasons.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("day_index", sa.Integer, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("synopsis", sa.Text, nullable=False),
        sa.Column("manifest_json", JSONB, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("released_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("season_id", "day_index",
                            name="uq_chapters_season_day"),
        sa.CheckConstraint(
            "status IN ('draft','generating','ready','ready_degraded','live','archived')",
            name="ck_chapters_status",
        ),
    )
    op.create_index("idx_chapters_status_release", "chapters",
                    ["status", "released_at"])


def downgrade():
    op.drop_index("idx_chapters_status_release", table_name="chapters")
    op.drop_table("chapters")
    op.execute("DROP INDEX IF EXISTS uniq_one_active_season")
    op.drop_table("seasons")
```

### `0005_cycles_transitions.py`

```python
"""cycles + state_transitions"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005"
down_revision = "0004"

def upgrade():
    op.create_table(
        "cycles",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("season_id", sa.BigInteger,
                  sa.ForeignKey("seasons.id"), nullable=False),
        sa.Column("chapter_id", sa.BigInteger,
                  sa.ForeignKey("chapters.id"), nullable=False),
        sa.Column("next_chapter_id", sa.BigInteger,
                  sa.ForeignKey("chapters.id"), nullable=True),
        sa.Column("state", sa.Text, nullable=False),
        sa.Column("state_entered_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("cycle_date", sa.Date, nullable=False),
        sa.CheckConstraint(
            "state IN ('PENDING_RELEASE','ESTRENO','RECEPCION_IDEAS',"
            "'FILTERING','VOTACION','GENERACION','FAILED')",
            name="ck_cycles_state",
        ),
        sa.UniqueConstraint("season_id", "cycle_date",
                            name="uq_cycles_season_date"),
    )
    op.create_index("idx_cycles_state", "cycles", ["state"])

    op.create_table(
        "state_transitions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("cycle_id", sa.BigInteger,
                  sa.ForeignKey("cycles.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("from_state", sa.Text, nullable=False),
        sa.Column("to_state", sa.Text, nullable=False),
        sa.Column("triggered_by", sa.Text, nullable=False),
        sa.Column("trigger_id", sa.Text, nullable=True),
        sa.Column("payload_json", JSONB, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "triggered_by IN ('cron','admin','retry','side_effect','watchdog')",
            name="ck_st_triggered_by",
        ),
    )
    op.create_index(
        "idx_st_cycle_recent",
        "state_transitions",
        ["cycle_id", sa.text("created_at DESC")],
    )
    op.execute(
        "CREATE UNIQUE INDEX uniq_st_trigger "
        "ON state_transitions(cycle_id, to_state, trigger_id) "
        "WHERE trigger_id IS NOT NULL"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS uniq_st_trigger")
    op.drop_index("idx_st_cycle_recent", table_name="state_transitions")
    op.drop_table("state_transitions")
    op.drop_index("idx_cycles_state", table_name="cycles")
    op.drop_table("cycles")
```

### `0006_system_flags.py`

```python
"""system_flags"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"

def upgrade():
    op.create_table(
        "system_flags",
        sa.Column("flag_key", sa.Text, primary_key=True),
        sa.Column("flag_value", JSONB, nullable=False),
        sa.Column("updated_by", sa.Text, nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.execute(
        "INSERT INTO system_flags (flag_key, flag_value, updated_by) "
        "VALUES ('kill_switch', '{\"on\": false, \"reason\": null}', 'migration')"
    )


def downgrade():
    op.drop_table("system_flags")
```

---

## Transition transaction (SQL outline)

The body of `cycle_executor.transition(cycle_id, to_state, trigger_id, triggered_by)`
runs:

```sql
BEGIN;
SET LOCAL lock_timeout = '2000ms';

-- 1. Acquire mutex for this cycle
SELECT pg_advisory_xact_lock(hashtext('cycle:' || :cycle_id::text));

-- 2. Read current state (fresh, under lock)
SELECT state, state_entered_at, season_id, chapter_id, next_chapter_id
  FROM cycles WHERE id = :cycle_id FOR UPDATE;

-- 3. Validate transition + min-dwell against the pure FSM function (out of band).
--    If invalid: ROLLBACK; raise IllegalTransition or TimeFenceViolation.

-- 4. Append the transition row. Idempotency UNIQUE catches replays.
INSERT INTO state_transitions
  (cycle_id, from_state, to_state, triggered_by, trigger_id, payload_json)
VALUES
  (:cycle_id, :from, :to, :triggered_by, :trigger_id, :payload)
ON CONFLICT (cycle_id, to_state, trigger_id)
  WHERE trigger_id IS NOT NULL
  DO NOTHING
RETURNING id;
-- IF returned 0 rows: ROLLBACK; return AlreadyApplied with the original row.

-- 5. Mutate the cycle.
UPDATE cycles
   SET state = :to,
       state_entered_at = now(),
       next_chapter_id = COALESCE(:next_chapter_id, next_chapter_id)
 WHERE id = :cycle_id;

-- 6. State-specific writes (run conditionally):
-- IF to_state = 'ESTRENO': UPDATE chapters SET status='live', released_at=now()
--                          WHERE id = (SELECT chapter_id FROM cycles WHERE id = :cycle_id);

COMMIT;

-- 7. Outside the transaction: schedule the relevant background task if any.
```

The combination of advisory lock + UNIQUE constraint guarantees: **at most one**
`state_transitions` row per `(cycle_id, to_state, trigger_id)`, **at most one**
side-effect spawn per transition, **no read tearing** of cycle state.
