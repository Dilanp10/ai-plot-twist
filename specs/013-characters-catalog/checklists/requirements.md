# Requirements Checklist: Characters Catalog

**Branch**: `013-characters-catalog` | **Date**: 2026-06-24

---

## Functional Requirements

- [ ] **FR-001** — `characters` table exists with all columns, CHECKs,
      partial index `idx_characters_active_sort`, and `updated_at` trigger
      from [data-model.md](../data-model.md). Alembic upgrade/downgrade
      round-trip is clean (integration test).

- [ ] **FR-002** — `GET /characters` returns active rows ordered by
      `(sort_order ASC, id ASC)`, JWT-protected, ETag + `Cache-Control:
      private, max-age=300`. Unauthenticated request → `401`. Matching
      `If-None-Match` → `304`. Empty catalog → `200 {"characters": []}`.
      All paths integration-tested.

- [ ] **FR-003** — Seed migration inserts 2 confirmed rows
      (`messi`, `bad-bunny`) idempotently via `INSERT … ON CONFLICT
      (slug) DO UPDATE`. Follow-up migration adds the rest once PO fills
      [research.md R-001](../research.md). Re-running the migration is a
      no-op.

- [ ] **FR-004** — `app.scripts.upload_static_assets` walks
      `assets/characters/*.webp`; rejects filenames not matching
      `^[a-z0-9-]{2,40}\.webp$` with a warning; uploads valid files to
      `static/characters/<basename>` with `Content-Type: image/webp`.
      Unit-tested with mocked uploader.

- [ ] **FR-005** — Response payload contains `photo_url` (absolute URL
      built from `R2_PUBLIC_BASE_URL + photo_r2_key`), not the raw R2
      key. Builder function is pure and unit-tested for trailing-slash
      safety.

- [ ] **FR-006** — Inactive characters (`active=FALSE`) are filtered at
      SQL via the partial index, not in Python. Disabling a character
      does NOT cascade-delete or invalidate existing twist proposals.
      Verified by integration test that sets `active=FALSE` on a row
      that has a `twist_proposals` reference and asserts the proposal
      still loads via JOIN.

- [ ] **FR-007** — `CharactersRepo.get_by_id_if_active(character_id)`
      returns `None` for inactive or missing ids. Used by module 005
      delta at twist submission time to reject FK to a hidden
      character. Unit-tested.

## Non-Functional Requirements

- [ ] **NFR-001** — `GET /characters` p95 ≤ 30 ms server-side under
      realistic load (≤12 active rows). Verified by `k6` smoke at
      module 010 integration time.

- [ ] **NFR-002** — Total JSON payload ≤ 4 KB at the upper bound
      (12 chars × ~300 B). No pagination in MVP.

- [ ] **NFR-003** — Each photo in R2 ≤ 80 KB. Total catalog ≤ 1 MB.
      Operator verifies via `file` + `ls -la` before upload (see
      [quickstart.md §2](../quickstart.md#2-provide-the-photos-locally)).

- [ ] **NFR-004** — All code passes `mypy --strict` and `ruff check`
      clean. No `# type: ignore` without an inline reason.

## Gates

- [ ] **Gate 1 — Zero-cost** — Storage within R2 + Neon free tier.
- [ ] **Gate 2 — Idempotency** — Seed `ON CONFLICT`. Endpoint is read-only.
      R2 PUT idempotent on key.
- [ ] **Gate 3 — TZ anchoring** — `created_at`/`updated_at` TIMESTAMPTZ.
- [ ] **Gate 4 — Provider abstraction** — N/A (no provider in this module).
- [ ] **Gate 5 — Determinism** — Stable `ORDER BY` + deterministic ETag.
- [ ] **Gate 6 — Resilience** — Empty catalog returns `200 []`, never errors.
- [ ] **Gate 7 — Soft delete** — `active=FALSE`, never DELETE. FK survives.
- [ ] **Gate 8 — Quota** — N/A.
- [ ] **Gate 9 — Privacy / PII** — Carve-out documented in
      [plan.md Gate 9](../plan.md#gate-9--privacy--pii) +
      [research.md R-003](../research.md). Closed-beta posture; takedown
      template ready.
- [ ] **Gate 10 — Observability** — `characters_fetched` log + `characters_total`
      metric.

## Acceptance

- [ ] All FR rows above checked.
- [ ] All NFR rows above checked.
- [ ] All Gate rows above checked OR carry a documented carve-out.
- [ ] `pytest apps/api/tests/` green.
- [ ] `mypy --strict` clean.
- [ ] `ruff check` clean.
- [ ] Module 005 delta can reference `characters.id` via FK without further
      changes here.
