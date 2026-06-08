# Feature Specification: Chapter Content Read API

**Feature Branch**: `004-chapters-content`
**Created**: 2026-06-07
**Status**: Draft
**Depends on**: `001-project-bootstrap`, `003-cycle-fsm`

## Summary

Expose the read-only public API that lets the PWA render the day's chapter: today's
manifest, an arbitrary archived chapter by `public_id`, and the active season's
metadata (including the public-safe portion of the bible). Asset URLs point to the
public Cloudflare R2 bucket so the PWA loads images and audio directly from the CDN
with no backend hop. The current FSM state and the four window timestamps are part of
every `today` response so the PWA can drive the UI without a second call.

No new tables. No mutations. Read-through the structures created by module 003. This
module is the **first user-facing surface** of the product.

## User Scenarios & Testing

### User Story 1 — User opens the PWA and sees today's chapter (Priority: P1)

A returning user opens the PWA on their phone at 14:00 ART. The home screen loads in
under 1 s with the day's chapter (3–4 panels + narration + cliffhanger) and shows the
"Tirá una idea" CTA because the cycle is in `RECEPCION_IDEAS`.

**Why this priority**: this is the only user-facing read path of the MVP loop. Until
this works, no user has a reason to visit.

**Independent Test**: with a `live` chapter in the DB and the cycle in
`RECEPCION_IDEAS`, hit `GET /api/v1/chapters/today` from a fresh curl. Validate the
response against `contracts/chapters.yaml`. Open the PWA in a browser; verify the
panels render and the CTA matches the state.

**Acceptance Scenarios**:

1. **Given** a cycle in any state with at least one chapter `status='live'`,
   **When** the client calls `GET /api/v1/chapters/today` without auth,
   **Then** HTTP 200 with the documented payload, including `chapter`, `season`,
   `cycle_state`, `windows`, and `panels[]` with public R2 URLs.

2. **Given** the response was served and the user's network is offline 5 minutes later,
   **When** the PWA reloads the same URL,
   **Then** the service worker serves the cached response (stale-while-revalidate
   enabled). No backend hit.

3. **Given** `cycle_state = "RECEPCION_IDEAS"`,
   **When** the PWA renders,
   **Then** the UI shows the "Tirá una idea" CTA and the countdown to
   `windows.submit_until`.

4. **Given** `cycle_state = "VOTACION"`,
   **When** the PWA renders,
   **Then** the UI shows the "Votá las mejores" CTA and the countdown to
   `windows.vote_until`. (CTA wiring lands with module 007.)

### User Story 2 — User reads an archived chapter (Priority: P2)

A user wants to re-read day 3 after day 7 is live. The PWA exposes a "Capítulos
anteriores" list and routes to `/chapter/<public_id>`.

**Acceptance Scenarios**:

1. **Given** chapter X has `status IN ('archived','live')`,
   **When** `GET /api/v1/chapters/{X.public_id}` is called,
   **Then** HTTP 200 with the same `chapter` shape (no `cycle_state`/`windows`
   fields — those are present only on `today`).

2. **Given** chapter X has `status IN ('draft','generating','ready','ready_degraded')`,
   **When** the endpoint is called,
   **Then** HTTP 404 — unreleased chapters are not addressable.

### User Story 3 — User reads season meta (Priority: P3)

For an "About this season" screen.

**Acceptance Scenarios**:

1. **Given** the active season has slug `s01-el-tunel`,
   **When** `GET /api/v1/seasons/s01-el-tunel` is called,
   **Then** HTTP 200 with `{season: {slug, title, bible_public, started_on, ended_on,
   chapter_count, current_day_index}}`. `bible_public` is the **public-safe subset**
   of `bible_json` (see FR-008).

2. **Given** the slug does not exist,
   **When** the endpoint is called,
   **Then** HTTP 404.

### User Story 4 — Kill-switch is active (Priority: P2)

The PO activated the kill-switch via module 003's CLI. The PWA's next call to
`/chapters/today` must reflect maintenance, not stale content.

**Acceptance Scenarios**:

1. **Given** `system_flags.kill_switch.on = TRUE` with `reason="…"`,
   **When** `GET /api/v1/chapters/today` is called,
   **Then** HTTP 503 with `{"code":"under_maintenance","reason":"…",
   "retry_after_seconds": 3600}`. The PWA shows a maintenance screen with the
   reason. `Cache-Control: no-store`.

### Edge Cases

- **No active season**: HTTP 503 with `{"code":"no_active_season"}` — different from
  kill-switch; the PWA can distinguish.
- **Cycle in `FAILED` state**: same as no active season for the purposes of this
  endpoint (`under_maintenance`). The PO is expected to flip the kill-switch on
  manually when investigating; this is a belt-and-suspenders fallback.
- **No chapter yet `live`** (e.g., the very first day before 12:00 ART): the *just-
  bootstrapped* chapter has `status='ready'` not `live`. The endpoint returns 404
  with `{"code":"no_live_chapter","first_release_at":"<iso>"}`. The PWA shows a
  countdown.
- **Auth header present but invalid**: ignored; treated as anonymous. No 401.
- **Auth header present and valid**: the response is identical (no auth-flavored
  fields in this module — those land with 007).
- **Manifest contains broken R2 URLs**: this module surfaces them as-is; the PWA
  shows a panel-level error and continues. Detection/repair owned by module 008.
- **Conditional request** with `If-None-Match` matching the current chapter's
  ETag: HTTP 304 Not Modified.
- **ETag stability**: ETag derived from `(chapter.public_id, cycle.state,
  chapter.released_at)`. State changes ⇒ ETag changes ⇒ caches refresh.

## Requirements

### Functional Requirements

- **FR-001**: `GET /api/v1/chapters/today` returns the chapter currently held by the
  active cycle's `chapter_id`. Auth optional.
- **FR-002**: The response body MUST conform to the `TodayResponse` schema in
  `contracts/chapters.yaml`, including:
  - `chapter`: `{id (uuid), day_index, title, synopsis, released_at, panels[],
    cliffhanger}`. `panels[i]` has `idx, image_url, image_blurhash (nullable),
    tts_url (nullable), narration, mood`.
  - `season`: `{slug, title}`.
  - `cycle_state`: one of the 7 FSM states.
  - `windows`: `{submit_until, vote_from, vote_until, next_release}` — all ISO 8601
    UTC datetimes. Server-computed from `cycle.state_entered_at + dwell`.
- **FR-003**: `panels[i].image_url` MUST be the **public R2 URL** stored in
  `chapter.manifest_json`. The backend MUST NOT proxy or rewrite asset bytes. URLs
  are absolute, https, point to the assets subdomain.
- **FR-004**: `GET /api/v1/chapters/{public_id}` returns the same `chapter` shape
  (without `cycle_state` / `windows`) for chapters with `status IN ('live',
  'archived')`. Unreleased → 404.
- **FR-005**: `GET /api/v1/seasons/{slug}` returns season meta + public bible.
  `bible_public` is the result of `bible_json` filtered by an allowlist
  (FR-008).
- **FR-006**: All three endpoints MUST check the `kill_switch` system flag (cached
  30 s in-process per module 003 R-005). When `on=true`, respond 503 with
  `under_maintenance`.
- **FR-007**: All three endpoints MUST set the following cache headers:

  | Endpoint | `Cache-Control` |
  |---|---|
  | `/chapters/today` | `public, max-age=60, stale-while-revalidate=600, must-revalidate` |
  | `/chapters/{id}` (archived) | `public, max-age=86400, immutable` |
  | `/chapters/{id}` (live) | `public, max-age=60, stale-while-revalidate=600` |
  | `/seasons/{slug}` | `public, max-age=300, stale-while-revalidate=3600` |
  | 503 responses | `no-store` |

- **FR-008**: The **public bible** is computed at response time by selecting only the
  allowlisted top-level keys: `setting`, `tone`, `characters`, `rules`. Any other
  key (e.g., a future `secrets`, `plot_twists_planned`) is excluded. The allowlist
  lives in `app/domain/bible_redaction.py` and is unit-tested.
- **FR-009**: All three endpoints MUST emit a `content_read` structured log with
  `endpoint, chapter_id?, season_slug?, cache_hint, status`.
- **FR-010**: All three endpoints MUST honor `If-None-Match` and return 304 with
  empty body when the ETag matches. ETag is the SHA-256 hex (truncated to 16 chars)
  of `(chapter.public_id, cycle.state, chapter.released_at)`.

### Non-Functional Requirements

- **NFR-001**: `/chapters/today` p95 < 100 ms (single DB query + Pydantic dump).
- **NFR-002**: `/chapters/{id}` p95 < 80 ms.
- **NFR-003**: `/seasons/{slug}` p95 < 80 ms.
- **NFR-004**: Endpoints MUST sustain 200 req/s on a single shared-cpu-1x Fly machine
  for 60 s without 5xx, per SDD G-5.

### Out of Scope (for this feature)

- Auth-flavored fields (`has_my_vote`, `my_twists_count`, etc.) — module 007/005.
- Presigned R2 URLs (assets are public). [OQ-CHAP-1]
- Listing all chapters of a season as a single response (paginated archive
  browser). [OQ-CHAP-2]
- Server-side rendering / SEO for shareable chapter pages. [OQ-CHAP-3]
- Image proxy / WebP transcoding (R2 serves what module 008 uploaded).
