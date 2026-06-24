# Implementation Plan: Characters Catalog

**Branch**: `013-characters-catalog` | **Date**: 2026-06-24 | **Spec**: [spec.md](./spec.md)
**Depends on**: `001-project-bootstrap`, `002-auth-invite-flow`

## Summary

Small read-only catalog module. One new SQL table (`characters`) + one
Alembic migration that creates it **and** seeds it + one public JWT-protected
endpoint (`GET /characters`) + extension of the existing
`upload_static_assets` script to walk `assets/characters/`. No new external
dependencies; no provider abstraction; no FSM hooks.

Consumed by:

- Module 005 delta — adds `character_id` FK NOT NULL on `twists`.
- Module 008 delta — reads `photo_r2_key` to feed Kling I2V (`image_url` arg).
- Module 010 delta — PWA renders the catalog as a horizontal carousel
  (`CharacterPicker.svelte`).

## Technical Context

**Languages/Versions**: Python 3.11.
**New deps**: none. Re-uses SQLAlchemy 2 async, Alembic, FastAPI, Pydantic v2,
`R2Uploader` from module 008/009.
**Storage**: PostgreSQL — one new table. R2 — `static/characters/*.webp`.
**Testing**: unit (serializer, repo, slug regex), integration (endpoint with
JWT, ETag 304, 401 unauth, partial index correctness).
**Performance Goals**: p95 ≤ 30 ms server-side (NFR-001).
**Constraints**: Gate 1 — within R2 + Neon free tier. Gate 7 — soft delete
via `active=FALSE` (no DELETE).
**Scale/Scope**: 8-12 characters in MVP; capped at ~50 by `Cache-Control`
sizing assumption.

## Constitution Check

### Gate 1 — Zero-cost
- [x] Storage: ≤1 MB R2 (catalog photos) + a trivial table in Neon. Both
      within free tier.
- [x] No new external API. No paid provider until module 012 delta enables Kling.

### Gate 2 — Idempotency
- [x] Seed migration uses `INSERT … ON CONFLICT (slug) DO UPDATE SET … ` so
      re-running on a partially-seeded DB is safe.
- [x] `GET /characters` is read-only and safe to retry.
- [x] `upload_static_assets` is idempotent on R2 (key derived from filename;
      same bytes → same `If-Match`-safe PUT).

### Gate 3 — TZ anchoring
- [x] `created_at TIMESTAMPTZ DEFAULT NOW()` follows convention.
- [x] No business logic depends on character timestamps.

### Gate 4 — Provider abstraction
- [x] N/A. Catalog is in-DB. R2 is accessed via existing `R2Uploader`.

### Gate 5 — Determinism
- [x] Ordering is stable: `ORDER BY sort_order ASC, id ASC`.
- [x] ETag is a deterministic hash of the ordered tuple
      `(id, slug, display_name, photo_r2_key, aspect_ratio, sort_order)` for
      active rows.

### Gate 6 — Resilience
- [x] Endpoint returns whatever the DB says; no fallback needed (the catalog
      is local data, not external).
- [x] If the table is empty (misconfigured deploy), endpoint returns
      `200 {"characters": []}` (the FE handles empty state).

### Gate 7 — Soft delete
- [x] `active BOOLEAN NOT NULL DEFAULT TRUE`. Disabling is `UPDATE active=FALSE`,
      never `DELETE`. FK references from `twists` survive.

### Gate 8 — Quota
- [x] N/A. Read-only public endpoint, JWT-rate-limited at the global level.

### Gate 9 — Privacy / PII
- [ ] **Legal review pending** — `display_name` + photos are public
      celebrities. Closed-beta scope (10-40 family/friends) → fair-use claim
      is plausible. Public launch would require explicit rights review.
      See [research.md R-003](./research.md).

### Gate 10 — Observability
- [x] Structured log `characters_fetched {count, etag, if_none_match_hit}`
      on every `GET /characters`.
- [x] Metric `characters_total{active}` exposed via existing `/metrics`.

## Tasks Outline

See [tasks.md](./tasks.md). Phases:

- **Phase 0** — DB schema + Alembic migration + seed (1 PR).
- **Phase 1** — Repo + serializer + endpoint + tests (1 PR).
- **Phase 2** — Extend `upload_static_assets` to walk `assets/characters/` (1 PR).
- **Phase 3** — Curate the 8-12 photos in `assets/characters/` and upload to
   R2 (operational, no code).

## Risks

- **R-1 — Image rights**: using likenesses of public celebrities for AI-generated
  derivative content can raise rights questions. Mitigation: closed-beta-only,
  document fair-use rationale, prepare opt-out plan if PO is contacted by an
  agent. Tracked in [research.md R-003](./research.md).

- **R-2 — Photo quality variance**: I2V output quality depends heavily on the
  seed photo (lighting, framing, resolution). Mitigation: research R-002
  defines acceptance criteria (1:1, 512×512, frontal portrait, neutral
  background); PO reviews each photo against checklist before upload.

- **R-3 — Slug stability**: once a character ships, its `slug` MUST NOT
  change (it is referenced by twists.character_id via id, but FE
  carousel URLs and CDN cache use slug). Mitigation: code review enforces
  "add new row, deprecate old via active=FALSE" — never rename.

## Acceptance for "Module done"

- [ ] All FRs in [checklists/requirements.md](./checklists/requirements.md) ✓.
- [ ] All Gates above either ✓ or carry a documented carve-out (Gate 9).
- [ ] `mypy --strict` clean.
- [ ] `ruff check` clean.
- [ ] `pytest apps/api/tests/` green (unit + integration).
- [ ] R2 contains all seed photos (verified via `aws s3 ls`-equivalent or CF dashboard).
- [ ] Module 005 delta can FK-reference `characters.id` in a follow-up PR
      without further changes here.
