# Phase 0 Research: Characters Catalog

**Branch**: `013-characters-catalog` | **Date**: 2026-06-24

---

## R-001 — Seed roster: which 8-12 characters?

**Question**: which exact characters ship in the MVP seed migration?

**Constraints**:

1. Globally recognizable (Argentinian + Latin American + global audiences).
2. Reasonably balanced across categories so the catalog feels broader than
   "10 footballers" (avoid mono-thematic catalog).
3. Photo availability with reasonable rights claim for closed-beta
   (see R-003).
4. Visual variability — different facial structures helps the PO sanity-check
   that I2V output is character-driven, not provider-driven.

**Proposed initial roster** (10 entries, kebab-case slugs):

| # | slug | display_name | category | rationale |
|---|---|---|---|---|
| 1 | `messi` | Lionel Messi | sports | PO-mentioned baseline |
| 2 | `bad-bunny` | Bad Bunny | music | PO-mentioned baseline |
| 3 | `[TBD-PO]` | [TBD-PO] | [TBD] | [TBD-PO] |
| 4 | `[TBD-PO]` | [TBD-PO] | [TBD] | [TBD-PO] |
| 5 | `[TBD-PO]` | [TBD-PO] | [TBD] | [TBD-PO] |
| 6 | `[TBD-PO]` | [TBD-PO] | [TBD] | [TBD-PO] |
| 7 | `[TBD-PO]` | [TBD-PO] | [TBD] | [TBD-PO] |
| 8 | `[TBD-PO]` | [TBD-PO] | [TBD] | [TBD-PO] |
| 9 | `[TBD-PO]` | [TBD-PO] | [TBD] | [TBD-PO] |
| 10 | `[TBD-PO]` | [TBD-PO] | [TBD] | [TBD-PO] |

**Action**: PO fills the 8 `[TBD-PO]` rows **before** the seed migration is
merged. Until then the seed file ships with placeholder rows in a feature
branch but is **not** included in the migration squashed into main.

---

## R-002 — Aspect ratio and photo specs

**Question**: what is the canonical photo geometry?

**Decision**: **1:1, 512×512 px, WebP, ≤80 KB.**

**Rationale**:

| Geometry | Pros | Cons |
|---|---|---|
| **1:1 (chosen)** | Mobile carousel cards are naturally square; matches all three Kling I2V output ratios via padding | Slight crop of full-body shots |
| 9:16 portrait | Matches default Kling output | Carousel cards become tall, fewer fit on screen |
| 16:9 landscape | Cinema-style framing | Carousel becomes a row of wide cards; UX worse on mobile |

**Acceptance checklist for each seed photo**:

- [ ] Square 512×512, WebP, ≤ 80 KB.
- [ ] Frontal portrait (face occupies 30-60 % of frame).
- [ ] Neutral or non-distracting background.
- [ ] Single subject (no group photos).
- [ ] Eyes open, mouth neutral or slight smile (drives more stable I2V output).
- [ ] No watermarks, no overlaid text.

The PO + lead dev manually review each photo against this list before
upload.

---

## R-003 — Image rights & legal posture for closed beta

**Question**: can we ship photos of public celebrities as seed images?

**Findings (informal)**:

- Closed beta scope (≤ 40 invited friends/family) puts the use squarely in
  "non-commercial, internal experimentation". US fair-use factors (purpose,
  amount, market effect) tilt strongly toward fair use under that scope.
- Argentina (production jurisdiction) — Art. 31 Ley 11.723 protects use of
  one's image; commercial exploitation requires consent. Internal closed
  group use is not commercial exploitation.
- Risk surfaces when (a) the group opens to the public, (b) generated
  videos leak to social media, or (c) a rights holder formally complains.

**Decisions**:

1. **MVP**: ship the catalog with closed-beta-only scope (constitution
   Gate 1 implicitly caps user count). PO + lead dev acknowledge the
   posture in this doc.
2. **Mitigation**: a single `slug='[redacted]'` migration template is
   pre-authored in the repo (see `infra/character_takedown_template.sql`,
   stub for now) that flips `active=FALSE` and replaces the photo with
   a placeholder, runnable in < 1 min if a takedown request arrives.
3. **Public launch (future)**: requires explicit legal review and likely
   a switch to original characters (commissioned art) or licensed
   likenesses. Tracked as [[open-question]] in SDD, not in this module's
   scope.

---

## R-004 — Endpoint authentication: JWT vs public?

**Question**: should `GET /characters` be open or JWT-gated?

**Decision**: **JWT-gated** via the existing dependency
`require_authenticated_user`.

**Rationale**:

1. The endpoint is consumed by the proposal form, which is itself behind
   auth. There is no anonymous flow that needs it.
2. JWT-gating means the existing per-IP rate-limit at the auth layer
   covers it — no extra rate-limit work needed here.
3. Hiding the catalog from unauth crawlers reduces (slightly) the surface
   of the image-rights claim in R-003 — only invited users see the list.

---

## R-005 — Cache strategy

**Question**: how aggressive should client + CDN caching be?

**Decision**:

- **Server**: ETag = `sha256` of the ordered tuple
  `(id, slug, display_name, photo_r2_key, aspect_ratio, sort_order)` of
  active rows. Recomputed in-memory on each request (catalog ≤ 12 rows;
  cost negligible).
- **Response**: `Cache-Control: private, max-age=300` (5 min).
- **Photos**: served from R2 public bucket via Cloudflare CDN; immutable
  via content-addressed paths (`static/characters/<slug>.webp` — slug
  never changes; replacing a photo bumps the version via a new migration
  that may flip slug if needed).

Implication: a fresh-seeded character appears in the PWA within ≤ 5 min
of the API restart, which is acceptable for an operation that runs at
most once per week in MVP.
