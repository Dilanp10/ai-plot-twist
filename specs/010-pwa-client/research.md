# Phase 0 Research: PWA Polish

**Branch**: `010-pwa-client` | **Date**: 2026-06-07

---

## R-001 — Router library

| Option | Size | Notes |
|---|---|---|
| **`svelte-spa-router` (chosen)** | ~3 KB gz | Hash routing; no history API gymnastics; explicit route table; well-tested |
| `tinro` | ~2.5 KB gz | Similar; declarative; small ecosystem |
| Roll-our-own | 1 KB gz | Tempting; rejected — testing routing edge cases adds up |
| SvelteKit | N/A | Way too much for this scope |

**Decision**: `svelte-spa-router`. Hash routing avoids the "Cloudflare Pages
serves wrong fallback for sub-paths" headache on PWA installs.

---

## R-002 — Service worker library

**Decision**: **`vite-plugin-pwa`** with `workbox-window` (already in plan from
module 001). Workbox owns the cache strategies, manifest injection, and update
choreography. No alternative was considered seriously.

**Strategy table** (from FR-003):

| URL pattern | Strategy | TTL |
|---|---|---|
| App shell (HTML/CSS/JS/icons) | Precache | until SW update |
| `GET /api/v1/chapters/today` | staleWhileRevalidate | 10 min |
| `GET /api/v1/seasons/*` | staleWhileRevalidate | 1 h |
| `assets.aiplottwist.example/*` | cacheFirst | 30 days |
| Any other API | networkOnly | — |

---

## R-003 — Install flow split (Android vs iOS)

**Question**: how do we offer install on both platforms cleanly?

**Android**: standard `beforeinstallprompt` capture, custom in-app card on
second visit (after the user has seen one chapter — earlier feels pushy).

**iOS**: no programmatic prompt. Detect:
- `/iPad|iPhone|iPod/.test(navigator.userAgent)`
- `!navigator.standalone` (not yet in standalone mode)

Show a bottom sheet with the 3-step Add-to-Home-Screen flow. Dismissible
sticky in `localStorage` for 14 days.

**Both platforms hidden when**:
- Already installed (display-mode standalone detected via
  `window.matchMedia('(display-mode: standalone)').matches`).
- Less than 1 chapter viewed (let users discover value first).

---

## R-004 — Strings management

**Question**: store UI strings inline or in a single table?

**Decision**: single `src/lib/strings.ts` with a typed `Strings` object:

```ts
export const STRINGS = {
  onboarding: {
    title: "Bienvenido a AI Plot Twist",
    submit: "Empezar",
    ...
  },
  today: { ... },
  ...
} as const;
```

**Rationale**: future translation is a swap of the file; tests assert no
inline strings in components (an ESLint rule for `<Literal>` JSX would catch
this, but Svelte tooling is weaker — we rely on review).

---

## R-005 — Theme tokens

**Decision**: CSS custom properties in `theme-tokens.css`. Single source of
truth for colors, spacing, motion durations.

```css
:root {
  --color-bg: #0f0f10;
  --color-fg: #f6f6f6;
  --color-accent: #ff6b6b;
  --color-focus: #ffb84d;
  --space-1: 4px;
  --space-2: 8px;
  --motion-fast: 150ms;
  --motion-normal: 250ms;
  --motion-slow: 400ms;
}

@media (prefers-reduced-motion: reduce) {
  :root {
    --motion-fast: 0ms;
    --motion-normal: 0ms;
    --motion-slow: 0ms;
  }
}
```

**Light theme**: via `prefers-color-scheme: light` media query. No
runtime toggle in MVP.

---

## R-006 — Client-log endpoint shape

**Question**: what's the minimum useful client error payload?

**Decision**:

```json
{
  "event": "unhandled_error" | "unhandled_rejection" | "boundary_caught" | "csp_violation" | "custom",
  "message": "string (≤ 500)",
  "stack": "string (≤ 3000)",
  "route": "/today" | "/vote" | ...,
  "user_agent": "string (≤ 200)",
  "app_version": "1.2.3-abc123",
  "timestamp": "ISO"
}
```

Total ≤ 4 KB. Backend logs without parsing internals.

**Why unauthenticated**: errors fire before / during the auth boot path; an
authenticated endpoint would miss the most interesting bugs.

**Why no DB**: structured logs (Fly) are sufficient at MVP scale; the PO greps.
Adding a `client_logs` table when the volume justifies it is trivial.

---

## R-007 — CSP rollout

**Question**: ship strict CSP from day 1 or start in report-only?

**Decision**: **report-only for the first week, then enforce**.

Phase 1 (PR): `Content-Security-Policy-Report-Only` header. Violations are
sent to `/internal/client-log` via `report-uri`.

Phase 2 (one week later): flip to `Content-Security-Policy` (enforcing).

**Why**: closed-beta users may surface third-party violations we don't
anticipate (e.g., a Telegram in-app browser injecting a script). Report-only
lets us collect the signal without breaking flows.

---

## R-008 — Bundle size budget

**Question**: how do we keep the bundle under 80 KB gz?

**Strategies**:

- No web fonts (system stack).
- No icon library (custom inline SVGs only).
- Lazy-loaded routes (`import('./routes/vote.svelte')` on demand).
- No moment/date-fns — use native `Intl.DateTimeFormat` + a 20-line
  countdown helper.
- No state-management lib (Svelte 5 runes are the store).
- `vite-plugin-pwa` adds workbox to a separate bundle (not main).

**Audit**: `vite-bundle-visualizer` plugin produces a treemap; checked in PR.

---

## R-009 — Lighthouse CI gate

**Decision**: PR triggers a workflow that:

1. Waits for the Cloudflare Pages preview deploy.
2. Runs `lhci autorun` against the preview URL with three URLs:
   `/`, `/today`, `/vote`.
3. Uploads results to LHCI Server (skipped in MVP; logs only).
4. Asserts budget per FR-015 user story 6.

Failure blocks merge.

---

## R-010 — A11y testing strategy

**Question**: Lighthouse Accessibility ≥ 95 alone is not enough — it catches
static violations but misses focus management and screen-reader UX.

**Decision**: combine three layers:

1. **Lighthouse a11y audit** in CI (catches static issues).
2. **Playwright `axe-core` integration** on key routes (catches dynamic
   issues).
3. **Manual smoke** before declaring done:
   - Keyboard-only navigation on each route.
   - VoiceOver on iOS Safari (manual; documented in quickstart).
   - TalkBack on Android Chrome (manual).

---

## R-011 — Sign-out completeness

**Question**: what exactly does sign-out clear?

**Decision**:

1. `IndexedDB.aiplottwist.auth` → `clearAuth()`.
2. `localStorage` → `localStorage.clear()` (wipes install-prompt dismissal
   too — fine).
3. Service worker → `(await navigator.serviceWorker.getRegistrations()).forEach
   (r => r.unregister())`.
4. Caches → `(await caches.keys()).forEach(k => caches.delete(k))`.
5. Hard navigation to `/onboarding` (full page reload, not SPA).

After sign-out, the user is in the same state as a fresh install. They need a
new invite code to come back.

---

## Open items

- **OQ-PW-1**: animated splash transitions. Decided: out of MVP.
- **OQ-PW-2**: A/B testing platform. Out of MVP (no framework chosen).
- **OQ-PW-3**: real Sentry / Datadog. Trigger: when client-log volume > 100/day.
