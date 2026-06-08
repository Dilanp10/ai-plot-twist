# Requirements Checklist: Chapter Content Read API

**Branch**: `004-chapters-content` | **Date**: 2026-06-07

---

## Functional Requirements

- [ ] **FR-001** — `GET /chapters/today` returns the chapter held by the active
      cycle. Tested in `test_chapters_today.py::test_happy_path`.
- [ ] **FR-002** — Response shape conforms to `TodayResponse`. Contract test
      (`test_chapters_contract.py`) validates a real response against
      `chapters.yaml`.
- [ ] **FR-003** — `panels[i].image_url` is the R2 public URL from
      `manifest_json`, untouched. Verified by snapshot test with a fixture
      manifest.
- [ ] **FR-004** — `GET /chapters/{public_id}` returns 200 for `live`/`archived`
      and 404 for `draft`/`generating`/`ready`/`ready_degraded`. Five named
      cases tested.
- [ ] **FR-005** — `GET /seasons/{slug}` returns season with redacted bible.
      Tested in `test_seasons_by_slug.py`.
- [ ] **FR-006** — All three endpoints respect the kill-switch. Three tests
      (one per endpoint) flip `system_flags.kill_switch.on = TRUE` and assert
      503 with `under_maintenance`.
- [ ] **FR-007** — Cache headers match the table in spec. One test per endpoint
      asserts the exact `Cache-Control` string for each scenario.
- [ ] **FR-008** — `bible_redaction.PUBLIC_BIBLE_KEYS` is the source of truth.
      Unit test asserts `redact(b)` ⊆ `b` and that any unknown top-level key
      is excluded.
- [ ] **FR-009** — `content_read` structured log emitted on every request with
      `endpoint, chapter_id?, season_slug?, cache_hint, status`. Verified by
      grep against test output.
- [ ] **FR-010** — `If-None-Match` returns 304 with empty body when ETag
      matches. Tested for `today` and for live/archived `chapters/{id}`.

## Non-Functional Requirements

- [ ] **NFR-001** — `/chapters/today` p95 < 100 ms (local k6).
- [ ] **NFR-002** — `/chapters/{id}` p95 < 80 ms.
- [ ] **NFR-003** — `/seasons/{slug}` p95 < 80 ms.
- [ ] **NFR-004** — 200 RPS for 60 s on a single Fly machine without 5xx
      (k6 attached to PR).

## Constitution Gates

- [ ] **Gate 1 — Zero-cost** — No new services. R2 free tier covers asset
      egress.
- [ ] **Gate 2 — Idempotency** — All reads are naturally idempotent.
- [ ] **Gate 3 — TZ anchoring** — Window timestamps are computed in UTC and
      emitted as ISO 8601 UTC. `cycle_date` is the ART calendar date.
- [ ] **Gate 4 — Provider abstraction** — N/A.
- [ ] **Gate 5 — Determinism** — Same `(cycle, chapter, kill_switch)` → same
      response + ETag. Snapshot test.
- [ ] **Gate 6 — Spanish UI / English code** — Code English; error `detail`
      strings Spanish.
- [ ] **Gate 7 — Soft delete** — N/A.
- [ ] **Gate 8 — Tests from day one** — Unit + integration + contract +
      snapshot + cache-header + 304 tests all ship in the PR.
- [ ] **Gate 9 — Trust boundaries** — No auth; no PII in responses; bible
      redaction defends against accidental key leaks.
- [ ] **Gate 10 — Observability** — `content_read` log on every request.

## Documentation

- [ ] Quickstart walked end-to-end on a clean dev box.
- [ ] `specs/README.md` marks module `done`; marks 005 `in-progress`.

## Sign-off

- [ ] Reviewer 1 (engineering)
- [ ] Reviewer 2 (PO)
