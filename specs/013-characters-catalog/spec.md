# Feature Specification: Characters Catalog

**Feature Branch**: `013-characters-catalog`
**Created**: 2026-06-24
**Status**: Draft
**Depends on**: `001-project-bootstrap`, `002-auth-invite-flow`

## Summary

Ship a small read-only catalog of fixed in-game **characters** (celebrities or
recognizable figures, e.g., Messi, Bad Bunny) that users must pick when
submitting a twist proposal. Each character has a single canonical
square photo stored in R2; the photo is later passed as **seed image** to the
I2V provider (Kling) during the GENERACION phase.

The module ships:

1. A new `characters` SQL table (id, slug, display_name, photo_r2_key,
   aspect_ratio, active, sort_order, created_at).
2. An Alembic migration that creates the table **and seeds 8-12 hard-coded
   characters** (the exact list is a TBD resolved in `research.md` R-001).
3. A public `GET /characters` HTTP endpoint that returns only `active=TRUE`
   rows, ordered by `(sort_order, id)`, with ETag + 5 min `Cache-Control`.
4. R2 layout `static/characters/<slug>.webp` (1:1, 512×512, ≤80 KB each).
   The existing `app.scripts.upload_static_assets` is extended to push these.

No HTTP write endpoints (no admin UI). No FSM integration. No business logic
beyond the read endpoint and the seed. Module 005 delta consumes this catalog
via a new `character_id` FK on `twist_proposals`; module 008 delta consumes
`photo_r2_key` to feed the I2V provider.

This module is **pure catalog infrastructure**. Admin UI for character CRUD
is explicitly **out of scope** for MVP (deferred to v0.2 per ADR-0008).

## User Scenarios & Testing

### User Story 1 — A user lists characters before submitting a twist (Priority: P1)

The PWA fetches the catalog at mount time of the proposal form, renders a
horizontal carousel of square cards (photo + display_name), and lets the
user pick one before sending the twist.

**Why this priority**: without the catalog, the user cannot submit any twist
(module 005 delta makes `character_id` NOT NULL).

**Independent Test**: integration test hits `GET /characters` with a valid
JWT and asserts response shape; unit test on the serializer.

**Acceptance Scenarios**:

1. **Given** the catalog has 10 active characters and 1 inactive
   (`active=FALSE`),
   **When** the client calls `GET /characters` with a valid JWT,
   **Then** the response contains exactly 10 entries, ordered by
   `(sort_order ASC, id ASC)`, the inactive one is omitted, each entry has
   `{id, slug, display_name, photo_url, aspect_ratio}`, and the response
   carries `ETag: "<sha256>"` + `Cache-Control: private, max-age=300`.

2. **Given** the client calls `GET /characters` with the previous `ETag` as
   `If-None-Match`,
   **When** the catalog has not changed,
   **Then** the server returns `304 Not Modified` with no body.

3. **Given** the client calls `GET /characters` **without** a JWT,
   **Then** the server returns `401 Unauthorized` (the catalog is gated by
   the same `device-token` JWT as the rest of the app).

### User Story 2 — Adding/disabling a character via migration (Priority: P2)

The PO wants to add a new celebrity to the catalog mid-season, or hide one
that became controversial, without touching application code.

**Why this priority**: low-frequency operation in MVP (closed beta). A new
Alembic migration with an `INSERT … ON CONFLICT DO UPDATE` (for add) or an
`UPDATE characters SET active=FALSE WHERE slug=…` (for hide) is sufficient.

**Acceptance Scenarios**:

1. **Given** a follow-up migration adds a new row with `slug='new-celeb'`
   and uploads `static/characters/new-celeb.webp`,
   **When** the API is restarted and `GET /characters` is called,
   **Then** the new entry appears at the position dictated by its
   `sort_order`. No other characters are affected.

2. **Given** a follow-up migration sets `active=FALSE` for an existing slug,
   **When** the API is restarted and `GET /characters` is called,
   **Then** the hidden character does not appear. **Existing twist proposals
   that reference it remain valid** (the FK is not invalidated; the row is
   merely hidden from the catalog).

### User Story 3 — Photo upload pipeline (Priority: P2)

The PO supplies 8-12 square photos in `assets/characters/` and runs
`upload_static_assets`. Each photo lands at `static/characters/<slug>.webp`
in R2 and becomes referenceable by the seed migration.

**Acceptance Scenarios**:

1. **Given** `assets/characters/messi.webp` exists locally and R2 credentials
   are configured,
   **When** the operator runs `uv run python -m app.scripts.upload_static_assets`,
   **Then** the file lands at `static/characters/messi.webp` in R2 with
   `Content-Type: image/webp`, and the script logs the public URL.

## Functional Requirements

- **FR-001** — `characters` table exists with the columns and constraints in
  [data-model.md](./data-model.md). Alembic migration is idempotent
  (`INSERT … ON CONFLICT (slug) DO UPDATE` for seeds).
- **FR-002** — `GET /characters` returns active characters only, ordered
  `(sort_order ASC, id ASC)`, JWT-protected, with stable ETag.
- **FR-003** — The seed migration inserts 8-12 rows. Exact list resolved in
  [research.md R-001](./research.md). Each `slug` is unique, kebab-case,
  matches regex `^[a-z0-9-]{2,40}$`.
- **FR-004** — `app.scripts.upload_static_assets` is extended: in addition
  to `placeholder.mp4` / `placeholder.webp`, it walks `assets/characters/*.webp`
  and uploads each to `static/characters/<basename>`.
- **FR-005** — The response payload contains `photo_url` (an absolute URL
  built as `R2_PUBLIC_BASE_URL + photo_r2_key`), **never** the raw R2 key
  (so the FE never has to know about R2 internals).
- **FR-006** — Inactive characters (`active=FALSE`) are filtered at the SQL
  level (partial index `WHERE active=TRUE`), not in Python. Disabling a
  character does **not** cascade-delete or invalidate existing twist
  proposals that reference it.

## Non-Functional Requirements

- **NFR-001** — `GET /characters` p95 ≤ 30 ms server-side (table fits in
  RAM; partial index covers the only query).
- **NFR-002** — Total catalog payload ≤ 4 KB JSON (12 chars × ~300 B each).
  No pagination needed at this scale.
- **NFR-003** — Each photo ≤ 80 KB. Total R2 storage ≤ 1 MB for the catalog,
  well within the free tier.
- **NFR-004** — Module is Gate 4 - clean: no provider abstraction needed
  (the catalog is in-DB; R2 is accessed via the existing `R2Uploader`).

## Out of Scope

- Admin UI / `POST /admin/characters` endpoint (deferred to v0.2).
- Per-season or per-series character subsets (catalog is global).
- Character voice / TTS persona overrides (handled by module 008 delta if needed).
- Multi-photo characters (one canonical photo per character in MVP).
- Localization of `display_name` (Spanish-only in MVP, matches UI language).
