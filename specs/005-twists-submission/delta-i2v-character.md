# Delta v2 — Character FK (I2V pivot)

**Applies to**: `specs/005-twists-submission/` | **Date**: 2026-06-24
**Triggered by**: SDD Ronda 7 (decisions #28-#34, ADR-0008); pivot from T2V free
to I2V Kling with a fixed character catalog.
**Read alongside**: the original `spec.md`, `data-model.md`, `tasks.md` in this
folder; `specs/013-characters-catalog/` (the new module this delta depends on).

---

## 1. What changes, what stays, what is new

### Stays untouched
- T-002 (`twist_content.py`) — NFKC normalization, length validation, slur
  filter unchanged. Content is still 5-280 chars.
- T-003 (`twist_quota.py`) — quota counts unchanged; `character_id` does
  **not** introduce a per-character quota in MVP.
- T-006 (`TwistSubmissionService.delete`, `.list_mine`) — unchanged; soft
  delete semantics, `deleted_by_user` status, `deleted_at`. Hiding a
  character via `active=FALSE` does **not** mass-delete its twists.
- T-008 (`DELETE /twists/{public_id}`) — unchanged.
- T-010 (race tests) — unchanged; quota race is on `(user_id, chapter_id)`,
  not character.
- T-011..T-014 (PWA stack) — the only change is in T-013 (`TwistModal.svelte`),
  where the new `CharacterPicker` is mounted; tracked in module 010 delta,
  not here.
- Soft delete (Gate 7), idempotency (Gate 2), TZ anchoring (Gate 3) — same.

### Changes (existing tasks modified)

- **T-001** — migration `0007_twists.py` is **not** edited in place. A new
  follow-up migration `0008_twists_character_id.py` adds the column. See
  §4 below for the SQL.
- **T-004** — `TwistsRepo.create()` gains a `character_id: int` parameter
  (positional, after `user_id`). Repo also gains
  `_validate_character_active(character_id)` that calls
  `CharactersRepo.get_by_id_if_active` (from module 013, T-003) before
  the INSERT.
- **T-005** — `TwistSubmissionService.submit()` accepts `character_id` from
  the request body; passes it through to `TwistsRepo.create`. The
  `forbidden_invalid_character` error path is added.
- **T-007** — `POST /twists/submit` request body adds `character_id: int`
  as **required**. Validation:
  - `422 invalid_request` if missing or not an integer.
  - `422 invalid_character` if the id is not present in `characters` or
    `active=FALSE` for that id.
- **T-009** — `GET /me/twists` response payload adds the joined
  `character` block (`{id, slug, display_name, photo_url}`). The JOIN
  is `LEFT JOIN characters ON twists.character_id = characters.id` —
  always populated for new twists; existing twists pre-delta carry the
  default character (see §4 migration strategy).
- **T-015** — SDD §5.5 patch wording reflects the new required field;
  done at the same time as the SDD Ronda 7 commit (already in main).

### New (added tasks)

- **T-017** — new migration `0008_twists_character_id.py` (see §4).
- **T-018** — extend the `TwistsRepo` integration tests:
  - Submit without `character_id` → service raises `InvalidRequest`.
  - Submit with `character_id` pointing to an inactive row →
    `InvalidCharacter` raised; no row inserted.
  - Submit with a valid `character_id` → row inserted with the FK; SELECT
    JOIN returns the character block.
- **T-019** — extend `GET /me/twists` response model + Pydantic test for the
  new `character` block shape.

---

## 2. New dependencies

None. This delta depends on **module 013** being merged so that:

- The `characters` table exists (FK target).
- `CharactersRepo.get_by_id_if_active` is importable.

No new Python packages.

---

## 3. Changed and new Functional Requirements

### FR-001 delta — POST body (replaces original FR-001 schema partially)

```python
# apps/api/app/schemas/twists.py — request model
class TwistSubmitIn(BaseModel):
    content: str = Field(..., min_length=5, max_length=280)
    character_id: int = Field(..., ge=1)              # NEW — required
    idempotency_key: str | None = Field(None, max_length=80)

    model_config = ConfigDict(frozen=True)
```

`character_id` is **required**. Omission → `422 invalid_request`.

### FR-NEW-1 — Character existence + active validation

Before the quota check (FR-004) and the INSERT, the service must:

1. Call `CharactersRepo.get_by_id_if_active(character_id)`.
2. If the call returns `None`, raise `InvalidCharacter` → mapped to
   `422 {"detail":"invalid_character"}` by the endpoint.

This happens **before** quota consumption: a rejected submission for an
invalid character does **not** burn quota. (Consistent with R-003 in
research.md — quota counts deleted twists, but never counts validation
failures.)

### FR-NEW-2 — `GET /me/twists` response includes character

```jsonc
{
  "twists": [
    {
      "public_id": "...",
      "content": "...",
      "status": "pending_review",
      "submitted_at": "...",
      "character": {
        "id": 1,
        "slug": "messi",
        "display_name": "Lionel Messi",
        "photo_url": "https://r2-public.example/static/characters/messi.webp"
      }
    }
  ]
}
```

The `character` block is **always populated** for new twists. For legacy
twists (if any survive the migration in §4), it falls back to the
default character row.

### FR-008 delta — `GET /me/twists` query

The repo's read query gains a `JOIN`:

```sql
SELECT t.public_id, t.content, t.status, t.submitted_at,
       c.id, c.slug, c.display_name, c.photo_r2_key
FROM twists t
LEFT JOIN characters c ON c.id = t.character_id
WHERE t.user_id = $1 AND t.chapter_id = $2 AND t.status <> 'deleted_by_user'
ORDER BY t.submitted_at DESC;
```

Index `idx_twists_user_chapter` continues to drive the WHERE; the JOIN
key is the FK index (B-tree on `character_id`, added by the new
migration).

---

## 4. Data model delta

### New column on `twists`

```sql
ALTER TABLE twists
    ADD COLUMN character_id BIGINT REFERENCES characters(id);

-- Backfill before flipping to NOT NULL.
-- See R-NEW-1 below for strategy.

ALTER TABLE twists
    ALTER COLUMN character_id SET NOT NULL;

CREATE INDEX idx_twists_character_id
    ON twists (character_id);
```

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `character_id` | BIGINT | NOT NULL REFERENCES `characters(id)` | Set at submission time. Survives `active=FALSE` on the character. |

### Migration `0008_twists_character_id.py` (sketch)

```python
def upgrade() -> None:
    # 1. Add nullable column.
    op.add_column('twists', sa.Column('character_id', sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        'fk_twists_character', 'twists', 'characters',
        ['character_id'], ['id'],
    )

    # 2. Backfill (strategy depends on R-NEW-1).
    op.execute("""
        UPDATE twists
        SET character_id = (SELECT id FROM characters ORDER BY sort_order, id LIMIT 1)
        WHERE character_id IS NULL
    """)

    # 3. Flip to NOT NULL + index.
    op.alter_column('twists', 'character_id', nullable=False)
    op.create_index('idx_twists_character_id', 'twists', ['character_id'])


def downgrade() -> None:
    op.drop_index('idx_twists_character_id', table_name='twists')
    op.drop_constraint('fk_twists_character', 'twists', type_='foreignkey')
    op.drop_column('twists', 'character_id')
```

### R-NEW-1 — Backfill strategy

**Question**: how to handle existing `twists` rows when adding `character_id NOT NULL`?

**Observed state** (2026-06-24, verified via `fly ssh console -C "psql ..."`
or local equivalent): **prod `twists` is empty** — the deploy went out
with `FakeVideoProvider` and the closed-beta cohort has not started
submitting yet.

**Decision** (contingent on observed state):

| Prod state at migration time | Strategy |
|---|---|
| `twists` empty (current) | Migration: add column NOT NULL DEFAULT NULL skipping backfill is impossible; instead, add nullable → no rows to backfill → flip NOT NULL → done. Indistinguishable from a fresh install. |
| `twists` has rows | Backfill assigns each existing row the **lowest-`sort_order` active character** (effectively "Messi" or whichever the PO put first). This is a degraded-but-valid state; the PO is informed that legacy twists carry a default character that they did not select. The alternative — soft-deleting all legacy rows — would burn user-submitted content and is rejected. |

**Action**: Before running the migration in prod, the operator runs
`SELECT count(*) FROM twists` and confirms which path applies. The
migration code path is the **same**; only the operational expectation
differs.

For local dev with seed twists, a manual `TRUNCATE twists` + reseed is
preferred over the backfill path; documented in
[`quickstart.md` §1 delta](#).

---

## 5. Tests delta

- **Unit** — `TwistSubmissionService.submit` raises `InvalidCharacter`
  when character is missing or inactive. Mocked repos.
- **Integration** — POST /twists/submit with `character_id` missing →
  422 `invalid_request`. With unknown id → 422 `invalid_character`. With
  inactive id → 422 `invalid_character`. With valid id → 200 + DB row +
  JOIN-fetched character block in subsequent `GET /me/twists`.
- **Race** — T-010 race test is rerun with the new field; expectation
  unchanged (quota race is on `(user_id, chapter_id)`, character FK is
  not a race participant).

---

## 6. OpenAPI / contracts delta

`specs/005-twists-submission/contracts/twists.openapi.yaml` (extend, do
not replace):

- `TwistSubmitIn` schema gets a required `character_id: integer` (≥1).
- `MyTwist` schema gets a required `character` block referencing the
  `Character` schema from
  `specs/013-characters-catalog/contracts/characters.openapi.yaml`
  (`$ref: '../013-characters-catalog/contracts/characters.openapi.yaml#/components/schemas/Character'`).
- New error code `invalid_character` listed in the 422 response.

---

## 7. Acceptance for "delta done"

- [ ] New migration `0008_twists_character_id.py` lands; upgrade/downgrade
      round-trip clean (extends T-001's round-trip test).
- [ ] `TwistsRepo.create` requires `character_id` (signature change visible
      in mypy).
- [ ] POST /twists rejects missing / unknown / inactive character with
      `422 invalid_character`; integration-tested.
- [ ] GET /me/twists returns the joined `character` block; integration-tested.
- [ ] Module 010 delta's `CharacterPicker` can mount against the new
      request schema without further changes here.
- [ ] R-NEW-1 (backfill strategy) confirmed against prod DB state before
      the migration is applied to prod.
- [ ] All checklist gates from the original 005 still pass; no new gate
      carve-outs.
