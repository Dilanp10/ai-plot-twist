# Phase 0 Research: Chapter Content Read API

**Branch**: `004-chapters-content` | **Date**: 2026-06-07

Mini-ADRs.

---

## R-001 — Public bucket vs presigned URLs vs signed-CDN-cookies

**Question**: How does the PWA access image/audio assets stored in R2?

| Option | Pros | Cons |
|---|---|---|
| **Public bucket (chosen)** | Zero backend involvement; PWA hits CDN directly; max cache efficiency | Anyone with the URL can fetch the asset (enumeration risk) |
| Presigned URLs (short-lived) | URL only valid for N minutes | Backend hop on every chapter render; PWA can't cache effectively |
| Signed CDN cookies | One auth event, asset reads stay anonymous | R2 doesn't natively support; would need a Worker |

**Decision**: **public bucket**.

**Rationale**: the threat model is "closed family-friends beta". The content is not
sensitive; the only abuse vector is hot-linking, which costs us zero (R2 egress is
free). To resist enumeration, the path scheme is
`/seasons/{season_slug}/{chapter_public_id}/{panel_idx}-{content_hash[0:8]}.webp`.
A user who knows day 7's chapter cannot guess day 8's because (a) `public_id` is a
random UUID and (b) `content_hash` is unguessable until generated. Module 008
implements this scheme.

**Trigger to revisit**: when public launch happens, evaluate Cloudflare CDN
signed-cookie approach.

---

## R-002 — Caching layers

**Question**: Where do we cache the `today` response?

We have three potential layers:

1. **Backend in-process** (Python LRU): per-machine; invalidates on every transition.
2. **HTTP Cache-Control + ETag**: respected by Cloudflare's edge, the user's browser,
   and the service worker.
3. **Service worker (PWA)**: stale-while-revalidate gives instant load even offline.

**Decision**: skip backend in-process cache; rely on layers 2 and 3.

**Rationale**: a Python LRU here would only help during synchronous bursts and would
need invalidation logic synchronized with `state_transitions`. The single DB query
is < 30 ms; we'd save little. Layers 2 and 3 do the heavy lifting and remove the
invalidation problem (ETag changes when the underlying tuple changes).

**Cache header recipe**:

```
Cache-Control: public, max-age=60, stale-while-revalidate=600, must-revalidate
ETag: "a1b2c3d4e5f60718"
Vary: Accept-Encoding
```

The 60 s `max-age` cap is to bound how stale the `cycle_state` can be when a
transition happens (users won't see a vote window open 60 s late on average).
`must-revalidate` prevents clients from serving stale-while-revalidate forever if
the network is fine.

---

## R-003 — Single SQL join vs ORM relationships

**Question**: How do we fetch the active cycle + its chapter + its season in one
round-trip?

| Option | Pros | Cons |
|---|---|---|
| **Single hand-rolled join (chosen)** | One round-trip; explicit; no lazy-load surprises | Slightly more code than ORM `.options(joinedload(...))` |
| SQLAlchemy `joinedload` | Idiomatic ORM | Two-row product cardinality risk if not pinned; harder to read perf |
| Three separate queries with in-process join | Easy mental model | Three round-trips; > p95 budget |

**Decision**: hand-rolled join. The query is stable and small:

```sql
SELECT
  c.id           AS cycle_id,
  c.state        AS cycle_state,
  c.state_entered_at,
  c.cycle_date,
  ch.public_id   AS chapter_public_id,
  ch.day_index,
  ch.title       AS chapter_title,
  ch.synopsis    AS chapter_synopsis,
  ch.manifest_json,
  ch.released_at,
  ch.status      AS chapter_status,
  s.slug         AS season_slug,
  s.title        AS season_title
FROM cycles c
JOIN chapters ch ON ch.id = c.chapter_id
JOIN seasons s   ON s.id = c.season_id
WHERE s.is_active = TRUE
LIMIT 1;
```

Index check: `seasons.uniq_one_active_season` partial unique index makes the
`WHERE s.is_active = TRUE` predicate index-only (cardinality 1).

---

## R-004 — Windows computation: server vs client

**Question**: Who computes the four window timestamps (`submit_until`, `vote_from`,
`vote_until`, `next_release`)?

**Decision**: **server**, exposed in the response in UTC.

**Rationale**: the client cannot reliably know the cron schedule (it's configurable
via env), the min-dwell rules, or the current `state_entered_at`. Computing
client-side would couple the PWA to backend internals. Server computation is one
function call per request, < 0.5 ms.

The formula (in `app/domain/windows.py`):

```python
def compute_windows(cycle: Cycle, now_utc: datetime) -> Windows:
    # next time the cycle will hit each named milestone
    # given the current state and CYCLE_TIMES env config
    ...
```

The function is pure (no DB) and unit-tested for every entry state.

---

## R-005 — ETag derivation

**Question**: What should the ETag be?

**Decision**: `sha256_hex( "|".join([chapter.public_id, cycle.state,
chapter.released_at.isoformat()]) )[:16]`.

**Rationale**: changes exactly when the user-visible content of the response
changes. `released_at` covers the case where module 008 republishes the same
chapter id with a corrected manifest (uncommon but possible). The 16-char prefix
gives 64 bits of collision resistance — sufficient for 100 users and a finite
chapter set.

**Trigger to revisit**: if module 008 ever updates `manifest_json` in place without
changing `released_at`, ETag would lie. Document: any manifest mutation must touch
`released_at` to invalidate caches.

---

## R-006 — Bible redaction strategy

**Question**: How do we expose the bible without leaking authorial secrets?

**Decision**: **top-level key allowlist** in `app/domain/bible_redaction.py`:

```python
PUBLIC_BIBLE_KEYS = frozenset({"setting", "tone", "characters", "rules"})

def redact(bible: dict) -> dict:
    return {k: v for k, v in bible.items() if k in PUBLIC_BIBLE_KEYS}
```

**Rationale**: simple, declarative, defensible. Future keys default to private. A
unit test asserts `redact(bible)` is always a subset of `bible` and that any
unknown key is excluded.

**Trigger to revisit**: when a future module adds a non-allowlisted key that
should be public, add it explicitly + update the test fixture. Don't auto-promote.

---

## R-007 — Error shape (RFC 7807 problem details)

We adopt the same shape used in modules 001–003. Specific codes added by this
module:

| `code` | HTTP | Meaning |
|---|---|---|
| `under_maintenance` | 503 | Kill-switch is on. Includes `reason`, `retry_after_seconds`. |
| `no_active_season` | 503 | No `seasons.is_active = TRUE` row exists. |
| `no_live_chapter` | 404 | Active cycle exists but no chapter has `status='live'` yet. Includes `first_release_at`. |
| `chapter_not_found` | 404 | `public_id` does not exist OR chapter is in pre-release status. |
| `season_not_found` | 404 | `slug` does not exist. |

The `code` field is the contract; `title` and `detail` may be tweaked without
breaking clients.

---

## R-008 — `stale-while-revalidate` semantics across layers

**Question**: How do the three layers (Cloudflare edge, browser HTTP cache, PWA SW)
interact with `stale-while-revalidate`?

**Decision**: tolerate the inconsistency. Specifically:

- Cloudflare edge respects `s-maxage` if present (we omit it; Cloudflare uses
  `max-age=60`).
- Browser HTTP cache respects `max-age=60` and may serve stale up to `60 + 600`
  with a background revalidate (per RFC 5861).
- The PWA service worker uses workbox's `staleWhileRevalidate` strategy which
  ignores HTTP cache directives and applies its own (configured to match: 60 s
  fresh, 10 min stale).

Net effect: a user's first visit hits backend; subsequent visits within 10 min are
instant from SW with a background refresh. This matches the target UX.

---

## Open items

- **OQ-CHAP-1**: signed CDN access (deferred to public-launch evaluation).
- **OQ-CHAP-2**: paginated archive endpoint `GET /chapters?season_slug=…&day_range=`.
- **OQ-CHAP-3**: SSR for shareable preview cards (OG tags). Probably a Cloudflare
  Worker on the Pages domain, not the API.
