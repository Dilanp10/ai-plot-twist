# Task Breakdown: Characters Catalog

**Branch**: `013-characters-catalog` | **Date**: 2026-06-24

---

## Phase 0 — DB schema + seed (1 PR)

### T-001 — Alembic migration: create `characters` table → `001-merged`

**Files**:
- `apps/api/alembic/versions/<rev>_add_characters_table.py` (new)

**Body**:
- `op.create_table('characters', …)` with all columns and `CHECK`s from
  [data-model.md](./data-model.md).
- `op.create_index('idx_characters_active_sort', 'characters', ['sort_order', 'id'], postgresql_where=sa.text('active = TRUE'))`.
- `op.execute("CREATE TRIGGER characters_set_updated_at …")` using the
  existing `set_updated_at()` function from module 001.
- Downgrade drops the trigger, index and table in reverse order.

**Test coverage**:
- `tests/integration/test_alembic_upgrade_downgrade.py::test_013_characters_round_trip`
  applies upgrade → asserts table + index + trigger exist → applies
  downgrade → asserts they are gone.

---

### T-002 — Seed migration (8-12 rows) → T-001

**Files**:
- `apps/api/alembic/versions/<rev>_seed_characters.py` (new)

**Body**:
- Single `op.execute()` block running `INSERT … ON CONFLICT (slug) DO UPDATE`
  for each row.
- **Until [research.md R-001](./research.md) is resolved** by the PO,
  this migration ships with **only the 2 PO-confirmed rows** (`messi`,
  `bad-bunny`). The remaining 8 are added in a follow-up migration once
  the PO fills the roster. **The endpoint with 2 rows is enough to
  unblock module 005 delta development.**
- Downgrade is `DELETE FROM characters WHERE slug IN (...)`.

**Test coverage**:
- Integration test asserts that after `upgrade()`, querying the table
  yields the seeded rows in `(sort_order, id)` order.
- Re-applying the seed is idempotent (the `ON CONFLICT … DO UPDATE`
  path is exercised).

---

## Phase 1 — Repo + endpoint (1 PR)

### T-003 — `CharactersRepo` async repo → T-001 [P with T-004]

**Files**:
- `apps/api/app/db/repos/characters_repo.py` (new)
- `apps/api/tests/unit/test_characters_repo.py` (new)

**API**:

```python
class CharactersRepo:
    def __init__(self, session: AsyncSession) -> None: ...

    async def list_active(self) -> list[CharacterRow]:
        """Return active rows ordered by (sort_order ASC, id ASC)."""

    async def get_by_id_if_active(self, character_id: int) -> CharacterRow | None:
        """Used by module 005 delta to validate FK at submission time."""
```

`CharacterRow` is a frozen `dataclass` with `id, slug, display_name,
photo_r2_key, aspect_ratio` (the columns the endpoint needs; `active`,
`sort_order`, timestamps are filtered out of the public surface).

**Test coverage**:
- `list_active` returns only `active=TRUE`, in the documented order.
- `get_by_id_if_active` returns `None` for inactive or missing id.

---

### T-004 — Pydantic v2 response models → T-001 [P with T-003]

**Files**:
- `apps/api/app/schemas/characters.py` (new)
- `apps/api/tests/unit/test_characters_schemas.py` (new)

**Models**:

```python
class CharacterOut(BaseModel):
    id: int
    slug: str
    display_name: str
    photo_url: HttpUrl
    aspect_ratio: Literal["1:1", "9:16", "16:9"]

    model_config = ConfigDict(frozen=True)


class CharactersList(BaseModel):
    characters: list[CharacterOut]
```

`photo_url` builder is a pure function in this module:

```python
def build_photo_url(photo_r2_key: str, public_base: str) -> str: ...
```

**Test coverage**:
- `build_photo_url` joins correctly (no double slashes; trailing-slash safe).
- `CharacterOut` rejects keys not under `static/characters/` at model-load
  time (defensive, even though DB CHECK already enforces it).

---

### T-005 — `GET /characters` endpoint → T-003, T-004

**Files**:
- `apps/api/app/api/characters.py` (new)
- `apps/api/tests/integration/test_characters_endpoint.py` (new)

**Behavior**:

1. JWT-protected via `require_authenticated_user` dependency.
2. Calls `CharactersRepo.list_active()`.
3. Builds `photo_url` per row using `settings.r2_public_base_url`.
4. Computes ETag = `sha256(json.dumps(rows_tuple, sort_keys=True)).hexdigest()`.
5. If `If-None-Match` header matches → returns `304 Not Modified`.
6. Else returns `200` with `Cache-Control: private, max-age=300` and the
   ETag header.
7. Emits `characters_fetched {count, etag, if_none_match_hit}` log.

**Test coverage**:
- 200 happy path with proper headers.
- 304 on matching `If-None-Match`.
- 401 without JWT.
- 401 with malformed JWT.
- Empty catalog → `200 {"characters": []}` + stable ETag of empty tuple.
- Inactive row absent.

---

### T-006 — Register endpoint in `main.py` → T-005

**Files**:
- `apps/api/app/main.py` (edit — add `app.include_router(characters_router)`)
- `apps/api/tests/integration/test_app_routes.py` (extend — assert
  `/characters` is registered with the expected dependency).

---

## Phase 2 — Static assets pipeline (1 PR)

### T-007 — Extend `upload_static_assets` to walk `assets/characters/` → T-002

**Files**:
- `apps/api/app/scripts/upload_static_assets.py` (edit)
- `apps/api/tests/unit/test_upload_static_assets.py` (extend)

**Behavior**:

1. After the existing 2 placeholder uploads, glob `assets/characters/*.webp`.
2. For each file, upload to `static/characters/<basename>`.
3. Reject files whose basename does not match `^[a-z0-9-]{2,40}\.webp$`
   (logs warning, skips, does not fail the run).
4. Logs `asset_uploaded filename=characters/<slug>.webp …` per file.
5. Exit codes unchanged.

**Test coverage**:
- Adds `assets/characters/test-char.webp` in a temp dir, mocks
  `R2Uploader.upload`, asserts the call args.
- Rejects invalid filenames (e.g., `Foo Bar.webp`) without failing the run.

---

## Phase 3 — Operational (no code)

### T-008 — Curate the 8-12 photos & upload → all above

- PO + lead dev review each candidate photo against
  [research.md R-002](./research.md) checklist.
- Photos land in `assets/characters/<slug>.webp`.
- Operator runs `upload_static_assets` once locally (creds from
  `.env.local`) and verifies the public URLs in a browser.
- For prod: run the script via `fly ssh console` against the deployed app
  (or via local with R2 creds — same flow as the existing placeholders).

---

## Cross-cutting

- All new code passes `mypy --strict` and `ruff check`.
- New files use English identifiers; Spanish only in user-facing strings
  (FE display_name values come from the seed, which is Spanish-friendly).
- No commented-out code, no TODOs without a tracked task id.
- Each PR includes a brief CHANGELOG entry under `docs/CHANGELOG.md` if
  that file exists (it does as of module 011).
