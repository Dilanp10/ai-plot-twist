# Quickstart: Characters Catalog

**Branch**: `013-characters-catalog` | **Date**: 2026-06-24
**Depends on**: modules 001, 002 merged; local DB running; R2 creds in
`.env.local` (the same ones already in prod, see `.env.example`).

---

## 1. Run the migration locally

```sh
cd apps/api
uv run alembic upgrade head
```

You should see in the output:

```
INFO  [alembic.runtime.migration] Running upgrade XYZ -> ABC, add_characters_table_and_seed
```

Verify the table and the seed:

```sh
uv run python -c "
import asyncio
from app.db.session import async_session

async def main():
    async with async_session() as s:
        rows = (await s.execute('SELECT slug, display_name, sort_order, active FROM characters ORDER BY sort_order, id')).all()
        for r in rows:
            print(r)

asyncio.run(main())
"
```

Expected: 8-12 rows ordered by `(sort_order, id)`, all `active=True`.

---

## 2. Provide the photos locally

Place 8-12 WebP files in `assets/characters/`, **one per seeded slug**:

```
assets/characters/messi.webp
assets/characters/bad-bunny.webp
...
```

Each must be **1:1, 512×512, ≤80 KB** (see [research.md R-002](./research.md)).

Quick validation:

```sh
file assets/characters/*.webp        # should report "Web/P image, ... 512x512"
```

---

## 3. Upload the photos to R2

```sh
cd apps/api
uv run python -m app.scripts.upload_static_assets
```

The script now walks `assets/characters/` in addition to the two
placeholders. Expected log lines:

```
INFO asset_uploaded filename=placeholder.mp4 bytes=… key=static/placeholder.mp4 url=https://…
INFO asset_uploaded filename=placeholder.webp bytes=… key=static/placeholder.webp url=https://…
INFO asset_uploaded filename=characters/messi.webp bytes=… key=static/characters/messi.webp url=https://…
INFO asset_uploaded filename=characters/bad-bunny.webp bytes=… key=static/characters/bad-bunny.webp url=https://…
...
```

Verify in your browser by opening one of the printed URLs.

---

## 4. Smoke the endpoint

Get a JWT for a test user (module 002 quickstart covers this; in short
`POST /auth/redeem-invite` with a dev code).

```sh
TOKEN="eyJhbGciOi..."  # from /auth/redeem-invite response

curl -i -H "Authorization: Bearer $TOKEN" http://localhost:8000/characters
```

Expected:

```http
HTTP/1.1 200 OK
ETag: "a3f9c2e1b48d..."
Cache-Control: private, max-age=300
Content-Type: application/json

{
  "characters": [
    {
      "id": 1,
      "slug": "messi",
      "display_name": "Lionel Messi",
      "photo_url": "https://<r2-public>/static/characters/messi.webp",
      "aspect_ratio": "1:1"
    },
    ...
  ]
}
```

Re-call with the returned ETag:

```sh
curl -i -H "Authorization: Bearer $TOKEN" \
     -H 'If-None-Match: "a3f9c2e1b48d..."' \
     http://localhost:8000/characters
```

Expected: `HTTP/1.1 304 Not Modified` with empty body.

Call without JWT:

```sh
curl -i http://localhost:8000/characters
```

Expected: `HTTP/1.1 401 Unauthorized`.

---

## 5. Disable a character (operational drill)

Write a follow-up migration:

```python
def upgrade() -> None:
    op.execute("UPDATE characters SET active=FALSE WHERE slug='messi'")
```

Apply and re-query `GET /characters`. The hidden character is absent.
Verify that an existing `twist_proposal` referencing him by id is **still
joinable** (FK survives because the row is not deleted).

---

## 6. Where this plugs into the rest of the system

- **Module 005 delta** adds `character_id INTEGER NOT NULL REFERENCES characters(id)`
  to `twist_proposals`. The POST /twists endpoint validates the id exists
  AND the row is active.
- **Module 008 delta**, during GENERACION, reads
  `proposal.character.photo_r2_key`, builds the absolute URL, and passes it
  as `image_url` to `KlingI2VProvider.generate()`.
- **Module 010 delta** fetches the catalog at mount of the proposal form
  and renders `CharacterPicker.svelte`.
