# Requirements Checklist: Generation Pipeline

**Branch**: `008-generation-pipeline` | **Date**: 2026-06-07

---

## Functional Requirements

- [ ] **FR-001** — DI registration overwrites `generation_pipeline_stub` from
      003. Startup integration test asserts.
- [ ] **FR-002** — Winner SQL = SDD §4.3 verbatim. Three named tiebreak
      tests (clear winner, two-way tie, three-way tie) + zero-row test.
      `winner_metadata` persisted with `tiebreak`, `runner_up_twist_id`.
- [ ] **FR-003** — Scriptwriter calls `LLMProvider.chat_json` with the
      versioned prompts. Auto-continue uses
      `scriptwriter_v1_auto.system.txt`. Prompt hash audit identical to 006.
- [ ] **FR-004** — Panel rendering uses `ImageProviderRouter`, seeds via
      `stable_hash(chapter_id, panel_idx)`, parallel up to
      `PANEL_CONCURRENCY`. Verified by integration test with FakeImage.
- [ ] **FR-005** — TTS optional, `edge-tts` voice = ART Spanish. Failure does
      NOT block panel.
- [ ] **FR-006** — `boto3` to R2 with retry policy; Cache-Control immutable.
      Mock-based unit test asserts call shape.
- [ ] **FR-007** — Persistence is a single transaction: INSERT chapter +
      UPDATE cycles.next_chapter_id.
- [ ] **FR-008** — Cycle transition to PENDING_RELEASE called after persist.
- [ ] **FR-009** — Deadline coordinator: `asyncio.wait` race with timeout
      task; cancellation propagates; degraded finalize path executed.
- [ ] **FR-010** — Partial failure → placeholder + `ready_degraded` +
      Discord alert.
- [ ] **FR-011** — All-panels-failed still results in `ready_degraded` (NOT
      FAILED) so chapter ships.
- [ ] **FR-012** — Two prompt files versioned + hash audit.
- [ ] **FR-013** — `POST /internal/generation/rerun` admin endpoint replaces
      manifest + bumps `released_at`.
- [ ] **FR-014** — `pnpm rerun-generation` CLI wraps endpoint.
- [ ] **FR-015** — All 9 structured log events emitted.
- [ ] **FR-016** — All settings exposed via env with documented defaults.

## Non-Functional Requirements

- [ ] **NFR-001** — Live pipeline p95 ≤ 50 min (nightly observation).
- [ ] **NFR-002** — Pipeline with FakeLLM + FakeImage (100 ms each) completes
      in ≤ 5 s.
- [ ] **NFR-003** — R2 upload p95 < 2 s per panel.
- [ ] **NFR-004** — Memory ≤ 200 MB. Profiled in a long-running test.

## Constitution Gates

- [ ] **Gate 1 — Zero-cost** — All providers free-tier; R2 free.
- [ ] **Gate 2 — Idempotency** — FSM idempotency + content-addressed R2 keys.
- [ ] **Gate 3 — TZ anchoring** — N/A.
- [ ] **Gate 4 — Provider abstraction** — Only imports `LLMProvider` (006)
      and `ImageProviderRouter` (009). Import-graph guards from 006 + 009
      apply.
- [ ] **Gate 5 — Determinism** — Winner selection deterministic; T2I seeds
      deterministic; **scriptwriter explicitly non-deterministic** (ADR-0004).
- [ ] **Gate 6 — Spanish / English** — Narrative Spanish; visual_prompt
      English; identifiers English.
- [ ] **Gate 7 — Soft delete** — Winner selector filters `status='approved'`,
      excluding `deleted_by_user`.
- [ ] **Gate 8 — Tests from day one** — Unit + 9 integration scenarios +
      live smoke.
- [ ] **Gate 9 — Trust boundaries** — LLM output validated against
      `ScriptwriterResponse` Pydantic; admin endpoint requires ADMIN_TOKEN.
- [ ] **Gate 10 — Observability** — Nine events emitted; Discord alerts on
      degraded/FAILED.

## Documentation

- [ ] Quickstart walked end-to-end with real providers.
- [ ] `docs/adr/0004-scriptwriter-creativity-exception.md` exists and links
      to research R-005.
- [ ] `specs/README.md` marks 008 `done`; 010 `in-progress`.

## Sign-off

- [ ] Reviewer 1 (engineering)
- [ ] Reviewer 2 (PO) — verify one real chapter end-to-end before sign-off.
