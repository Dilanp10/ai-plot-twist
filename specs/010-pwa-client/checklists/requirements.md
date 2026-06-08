# Requirements Checklist: PWA Polish

**Branch**: `010-pwa-client` | **Date**: 2026-06-07

---

## Functional Requirements

- [ ] **FR-001** — `App.svelte` implements the app shell. Bottom-nav has 4
      tabs. Tested in `app-shell.test.ts`.
- [ ] **FR-002** — Initial route resolver picks `/today`/`/vote`/etc. based on
      `cycle_state`. Five named cases tested.
- [ ] **FR-003** — Service-worker strategy verified in `e2e/offline-flow.spec.ts`
      (precache + SWR + cacheFirst for assets).
- [ ] **FR-004** — Android `beforeinstallprompt` captured; iOS sheet shows for
      iOS UA + not-standalone. Two e2e tests (`install-android`,
      `install-ios`).
- [ ] **FR-005** — `/settings` exposes the documented widgets. Manual review +
      e2e screenshot.
- [ ] **FR-006** — Sign-out clears the documented surfaces. Verified in
      `sign-out.spec.ts` by post-sign-out inspection of IDB / localStorage /
      SW registrations.
- [ ] **FR-007** — Accessibility: semantic HTML, focus rings, 44×44 tap
      targets, alt text, 4.5:1 contrast, reduced motion. Lighthouse a11y ≥ 95;
      `axe-core` Playwright integration green.
- [ ] **FR-008** — Loading skeletons render for ≤ 500 ms gates on each route.
      Visual snapshots in PR.
- [ ] **FR-009** — `<ErrorBoundary>` renders fallback + POSTs `client_log`.
      `error-boundary.spec.ts`.
- [ ] **FR-010** — Global error/rejection handlers throttle to ≤ 10/min and
      POST. Unit test on `client-logger.ts`.
- [ ] **FR-011** — `POST /internal/client-log` accepts payload, rejects 4 KB+,
      rate-limits per IP. Integration test.
- [ ] **FR-012** — Manifest finalized with all icons + theme color.
      Lighthouse PWA ≥ 90.
- [ ] **FR-013** — `index.html` has all viewport / theme / Apple meta. CSP
      `_headers` file present.
- [ ] **FR-014** — Main bundle ≤ 80 KB gz (audit step in CI).
- [ ] **FR-015** — `lighthouse-ci.yml` workflow blocks PRs that fail budget.

## Non-Functional Requirements

- [ ] **NFR-001** — FCP ≤ 2 s on Slow 3G.
- [ ] **NFR-002** — TTI ≤ 3.5 s on Slow 3G.
- [ ] **NFR-003** — Cold install → first paint ≤ 1.5 s.
- [ ] **NFR-004** — SW precache ≤ 200 KB gz.
- [ ] **NFR-005** — `/internal/client-log` p95 < 50 ms.

## Constitution Gates

- [ ] **Gate 1 — Zero-cost** — No new paid services. Lighthouse CI free.
- [ ] **Gate 2 — Idempotency** — Install + sign-out + client-log all
      idempotent.
- [ ] **Gate 3 — TZ anchoring** — UI uses `Intl.DateTimeFormat({timeZone:
      'America/Argentina/Buenos_Aires'})`.
- [ ] **Gate 4 — Provider abstraction** — N/A.
- [ ] **Gate 5 — Determinism** — Routing decision deterministic.
- [ ] **Gate 6 — Spanish UI / English code** — `strings.ts` is the source of
      truth.
- [ ] **Gate 7 — Soft delete** — Sign-out wipes client state only; user row
      preserved.
- [ ] **Gate 8 — Tests from day one** — Vitest + Playwright + Lighthouse.
- [ ] **Gate 9 — Trust boundaries** — CSP enforced (after Phase 2); client-
      log size-limited and rate-limited.
- [ ] **Gate 10 — Observability** — Client errors flow to backend log.

## Manual smoke (real devices)

- [ ] Tested on Android (Pixel or equivalent) — install + offline + a11y.
- [ ] Tested on iOS (iPhone) — instructions sheet, standalone mode, VoiceOver.

## Documentation

- [ ] Quickstart walked end-to-end on real devices.
- [ ] `docs/adr/0005-csp-rollout.md` exists.
- [ ] `specs/README.md` marks 010 `done`; 011 `in-progress`.

## Sign-off

- [ ] Reviewer 1 (engineering)
- [ ] Reviewer 2 (PO) — must test from their own phone before sign-off.
