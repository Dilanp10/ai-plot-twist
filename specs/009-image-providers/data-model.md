# Data Model: ImageProvider Abstraction

**Branch**: `009-image-providers` | **Date**: 2026-06-07

**No tables, no migrations.** This module is pure infrastructure.

What this file documents instead: the **path-derivation contract** for R2 keys.
This is the only "data shape" decision that crosses module boundaries (module
008 uses it; module 004 reads URLs constructed from this scheme).

---

## R2 path contract

```
seasons/{season_slug}/{chapter_public_id}/{panel_idx}-{content_hash}.{ext}
```

| Segment | Source | Constraints |
|---|---|---|
| `seasons/` | Constant prefix. | Allows future siblings (e.g., `samples/`). |
| `{season_slug}` | `seasons.slug` from DB | Lowercase + hyphen-separated; max 40 chars. |
| `{chapter_public_id}` | `chapters.public_id` (UUID v4) | Unguessable; prevents future-chapter enumeration. |
| `{panel_idx}` | Integer 1..N | Human-readable in URLs and ops logs. |
| `{content_hash}` | `sha256(image_result.bytes_)[:8]` (8 hex chars) | 32 bits of resistance against accidental cache collision; deterministic. |
| `{ext}` | Map from `image_result.mime_type` | `webp / png / jpg`. |

**Example**:
```
seasons/s01-el-tunel/9f3a3b5f-7e2c-4d4f-a1b2-c3d4e5f60718/2-a1b2c3d4.webp
```

**Invariants**:

1. **Deterministic**: same input → same path. Allows R2 PUT idempotency.
2. **Content-addressed for the variable part**: re-render of the same panel
   with a tweaked prompt produces a *different* `content_hash` and therefore a
   *different* path. Caches at any level (Cloudflare CDN, browser, SW) are
   invalidated by URL change.
3. **Enumeration resistance**: knowing day-7's URL does not let an attacker
   guess day-8's, because (a) `chapter_public_id` is a fresh UUID and (b) the
   `content_hash` is unguessable until generation completes.

---

## Helper API

```python
# app/providers/image/paths.py
from app.providers.image.base import ImageResult
import hashlib

_MIME_TO_EXT = {
    "image/webp": "webp",
    "image/png":  "png",
    "image/jpeg": "jpg",
}

def compute_r2_path(
    season_slug: str,
    chapter_public_id: str,   # UUID as str (lowercase, with hyphens)
    panel_idx: int,
    image_result: ImageResult,
) -> str:
    ext = _MIME_TO_EXT[image_result.mime_type]
    content_hash = hashlib.sha256(image_result.bytes_).hexdigest()[:8]
    return (
        f"seasons/{season_slug}/{chapter_public_id}/"
        f"{panel_idx}-{content_hash}.{ext}"
    )
```

**Test coverage** (in `test_image_paths.py`):
- Same `(slug, uuid, idx, result)` → same path (idempotency).
- Different bytes → different hash → different path.
- Unknown mime type → `KeyError` (defensive; consumer must catch).
- Path matches the regex `^seasons/[a-z0-9-]+/[0-9a-f-]{36}/\d+-[0-9a-f]{8}\.(webp|png|jpg)$`.

---

## What this module does NOT define

- The **public URL** prefix (`https://assets.aiplottwist.example/...`). That's
  R2 / Cloudflare config, owned by module 001's `infra/`.
- The **R2 upload** code. That's `app/infra/r2_uploader.py`, owned by module 008.
- The **caching headers** on the asset. R2 sets them; the consumer
  configures via R2 dashboard or terraform-equivalent.

---

## Future schema implications

When the assets become numerous (≥ 10 000), we might add a `chapter_assets`
table to enumerate every uploaded blob with `content_hash`, `provider`, `model`,
`generated_at`. The hash-prefixed path scheme will still hold; the table will
just index it. **Not in MVP.**
