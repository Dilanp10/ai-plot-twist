# Feature Specification: PWA Polish, Install, Offline, Accessibility

**Feature Branch**: `010-pwa-client`
**Created**: 2026-06-07
**Status**: Draft
**Depends on**: `002-auth-invite-flow`, `004-chapters-content`,
                `005-twists-submission`, `007-voting`

## Summary

Promote the PWA from "works on my machine in Chrome" to "installable, offline-
graceful, accessible, observable" on Android and iOS. Wraps up the frontend with:

1. **App shell + global navigation** between `/today`, `/vote`, `/me`,
   `/settings` (route auto-selected by `cycle_state` on landing).
2. **Service worker strategy** that precaches the app shell + static assets and
   runtime-caches `GET /chapters/today` with stale-while-revalidate, so an
   offline open still shows yesterday's chapter.
3. **Install flow**: Android `beforeinstallprompt` capture + iOS "Add to Home
   Screen" instructions screen.
4. **Settings screen**: read-only display of identity (display_name + invite
   code redacted), sign-out (clears IndexedDB), notification opt-in stub (real
   plumbing in 011).
5. **Accessibility minimums** — WCAG 2.2 AA: semantic HTML, keyboard
   navigation, focus management, ARIA where needed, `prefers-reduced-motion`
   respected.
6. **Loading skeletons + error boundaries** so the PWA never shows a blank
   screen.
7. **Client error reporting**: `POST /api/v1/internal/client-log` (no auth
   required; rate-limited per IP) so we can see what's breaking on real
   devices.
8. **Performance budget enforced in CI** via Lighthouse CI (mobile profile,
   throttled 3G).

No new business logic; one new endpoint (`/internal/client-log`); no new DB
tables.

## User Scenarios & Testing

### User Story 1 — Install on Android, open offline next day (Priority: P1)

A family member on a Pixel opens the PWA URL, gets an "Install AI Plot Twist"
prompt, taps Install. The next day they open from the home-screen icon, the
service worker serves yesterday's chapter from cache, network reconnects, the
app fetches the new chapter.

**Why this priority**: closed-beta retention depends on the PWA being treated
like a real app. A bookmark-only experience loses people.

**Acceptance Scenarios**:

1. **Given** Chrome on Android meets PWA installability criteria,
   **When** the user visits the app for the second time within a session,
   **Then** an in-app banner offers Install (captured `beforeinstallprompt`
   event); accepting installs the PWA.

2. **Given** the PWA is installed and the user opens it with no network,
   **When** the home route loads,
   **Then** the app shell renders within 500 ms (from cache), and the most
   recent cached `/chapters/today` payload renders with an "Sin conexión"
   indicator.

3. **Given** network returns,
   **When** the SW background-revalidates,
   **Then** the page swaps to the live chapter without a hard reload.

### User Story 2 — Install on iOS via instructions (Priority: P1)

An iPhone user opens the URL in Safari. We can't trigger an install prompt on
iOS, so we show platform-specific instructions.

**Acceptance Scenarios**:

1. **Given** Safari on iOS detected via `navigator.userAgent`,
   **When** the user visits and is NOT yet running standalone,
   **Then** a dismissable bottom-sheet shows "Compartí → Agregar a Pantalla de
   Inicio" with an illustrated 3-step.

2. **Given** the user dismisses,
   **When** they return next session,
   **Then** the sheet does NOT re-appear (sticky in `localStorage`) until they
   open `/settings → Cómo instalar`.

### User Story 3 — User signs out (Priority: P2)

A user wants to test as a different identity, or hand the phone to someone
else.

**Acceptance Scenarios**:

1. **Given** the user is on `/settings`,
   **When** they tap "Cerrar sesión",
   **Then** a confirmation dialog appears warning that the device_secret will
   be wiped and a new invite will be required. On confirm: IndexedDB
   `aiplottwist.auth` cleared, the SW caches cleared, route navigates to
   `/onboarding`.

### User Story 4 — Keyboard and screen-reader navigation (Priority: P2)

A user navigates the app with only Tab/Shift+Tab + Enter (no mouse) and with
VoiceOver / TalkBack enabled.

**Acceptance Scenarios**:

1. **Given** focus on the page,
   **When** the user Tabs through the home route,
   **Then** focus order matches reading order (header → state badge →
   countdown → panel 1 → panel 2 → … → CTA). All interactive elements have
   visible focus rings.

2. **Given** VoiceOver active,
   **When** the user lands on the home,
   **Then** the page announces: heading level 1 (chapter title), then the
   synopsis, then panels each with their narration as `alt` text on the image
   plus the narration as a visible caption.

3. **Given** `prefers-reduced-motion: reduce`,
   **When** the page renders,
   **Then** all transitions ≥ 200 ms are disabled; countdowns still update
   without flicker.

### User Story 5 — Error boundary catches a crash (Priority: P2)

A panel image fails to load AND the narration text has a render error in some
edge case.

**Acceptance Scenarios**:

1. **Given** a component throws during render,
   **When** the error boundary catches,
   **Then** the affected sub-tree renders a "Algo salió mal en este pedacito"
   card; the rest of the app keeps working; a `client_log` event is POSTed to
   the backend with the error stack and route.

### User Story 6 — Lighthouse perf budget passes in CI (Priority: P1)

Every PR runs Lighthouse on the deployed Pages preview; the budget gate blocks
merge.

**Acceptance Scenarios**:

1. **Given** a PR triggers the `lighthouse-ci.yml` workflow,
   **When** Lighthouse runs against the preview URL with mobile profile +
   throttled 3G,
   **Then** these scores MUST hold:
   - Performance ≥ 85
   - Accessibility ≥ 95
   - Best Practices ≥ 95
   - PWA ≥ 90
   - First Contentful Paint ≤ 2.0 s
   - Largest Contentful Paint ≤ 3.5 s
   - Total Blocking Time ≤ 200 ms
   - Bundle size for main route ≤ 80 KB gz (after the placeholder is
     replaced by real content).

### User Story 7 — Client error logged to backend (Priority: P2)

A bug fires for one specific user. We want the PO to see it without asking the
user to send a screenshot.

**Acceptance Scenarios**:

1. **Given** the PWA catches an unhandled rejection or error,
   **When** the global handler runs,
   **Then** it POSTs a sanitized `{event, message, stack, route, user_agent,
   app_version, timestamp}` to `/api/v1/internal/client-log`. The endpoint is
   unauthenticated (closed beta, low value target) but IP-rate-limited via the
   bucket from module 002 (5/min/IP). Backend structured-logs the payload as
   `client_log_received` without persisting to DB.

### Edge Cases

- **Service worker update**: when a new SW is detected, an in-app toast says
  "Actualización disponible — recargá" with a button. Auto-update on next cold
  start.
- **IndexedDB write fails** (user disabled storage): show a banner "Activá
  almacenamiento para no perder tu sesión"; sign-in still proceeds in memory
  for the session.
- **Slow first paint over 3G**: skeleton screens render within 500 ms even if
  data takes 5+ s.
- **PWA opened in iOS in-app browser (Instagram, Twitter)**: install prompt
  cannot work; show a "Abrir en Safari" hint.
- **User mashes the install button**: idempotent — the second tap is a no-op.
- **CSP violation**: report-only header collects violations to
  `/internal/client-log` via the standard `report-to` directive (best effort).
- **Backend returns 503 (kill-switch)**: app-shell renders, displays a
  maintenance card with the reason from the response.

## Requirements

### Functional Requirements

- **FR-001**: Root layout (`App.svelte`) implements the app shell: top bar
  with chapter day index + state badge; bottom tab nav (Today / Vote / Me /
  Settings); content slot. Routing via `svelte-spa-router` or equivalent
  ≤ 5 KB lib.
- **FR-002**: Initial route is selected by reading `cycle_state` from the
  most-recent `/chapters/today` response (cached or fresh):
  - `ESTRENO | RECEPCION_IDEAS` → `/today`.
  - `VOTACION` → `/vote`.
  - `FILTERING | GENERACION | PENDING_RELEASE` → `/today` with a "calmá los
    bidones, ya viene lo nuevo" state.
  - `FAILED` or kill-switch → `/today` maintenance screen.
- **FR-003**: Service worker strategy via `vite-plugin-pwa` (workbox):
  - **Precache**: app shell HTML, CSS, JS, icons, manifest.
  - **Runtime**:
    - `GET /api/v1/chapters/today` — `staleWhileRevalidate`, 10 min max age.
    - `GET /api/v1/seasons/*` — `staleWhileRevalidate`, 1 h.
    - `GET assets.aiplottwist.example/*` — `cacheFirst`, 30 days.
    - All other API requests — `networkOnly`.
- **FR-004**: Install flow:
  - Android: capture `beforeinstallprompt`, hide the default mini-bar, show a
    custom in-app card after the user has viewed at least one chapter.
  - iOS: detect `navigator.standalone === false` AND iOS UA → show
    instructions sheet.
- **FR-005**: `Settings` route exposes:
  - Display name (read-only).
  - Invite code (last 4 chars only).
  - "Notificaciones" toggle (UI stub; real plumbing in module 011).
  - "Cerrar sesión" button with confirmation.
  - "Cómo instalar" link to the install instructions sheet.
  - App version + commit SHA + build date.
- **FR-006**: Sign-out clears `IndexedDB.aiplottwist.auth`, clears
  `localStorage`, calls `navigator.serviceWorker.getRegistrations()` then
  `unregister` (so the next install is a clean install), and routes to
  `/onboarding`.
- **FR-007**: Accessibility:
  - Semantic HTML: `<main>`, `<nav>`, `<header>`, headings hierarchy.
  - Visible focus rings (`outline: 2px solid var(--focus)` on `:focus-visible`).
  - All interactive elements ≥ 44×44 px tap target (WCAG 2.2 AAA).
  - All images have meaningful `alt` (panel image alt = narration; decorative
    images `alt=""`).
  - Color contrast ≥ 4.5:1 for text.
  - `prefers-reduced-motion: reduce` honored (transitions ≤ 200 ms or
    disabled).
  - Lighthouse accessibility score ≥ 95.
- **FR-008**: Loading skeletons for `today`, `vote`, `me/twists` routes
  rendered while the corresponding store is `status='loading'`. No spinner-
  only screens.
- **FR-009**: Error boundary `<ErrorBoundary>` Svelte component wraps each
  route's main view. On catch: renders fallback UI, sends `client_log`.
- **FR-010**: Global handlers `window.addEventListener('error',...)` and
  `window.addEventListener('unhandledrejection',...)` collect events; throttled
  to ≤ 10/min via a local queue; POSTed to `/internal/client-log`.
- **FR-011**: `POST /api/v1/internal/client-log` endpoint:
  - Unauthenticated.
  - IP-rate-limited via the bucket from module 002 (5 reqs/min/IP).
  - Body: `{event: string, message: string?, stack: string?, route: string?,
    user_agent: string, app_version: string, timestamp: string}`.
  - Maximum payload 4 KB; reject 413 above.
  - Backend logs as `client_log_received {fields...}`. No DB persistence.
- **FR-012**: PWA manifest (`manifest.webmanifest`) is final:
  - `name`, `short_name`, `description` in Spanish.
  - `theme_color` matches the app's primary color.
  - `background_color` matches the splash.
  - `display: "standalone"`.
  - `icons`: 192, 512, 192 maskable, 512 maskable.
- **FR-013**: Top-level `index.html` has:
  - `<meta name="viewport" content="width=device-width, initial-scale=1,
    viewport-fit=cover">`.
  - `<meta name="theme-color" content="...">` matching manifest.
  - Apple-specific meta: `apple-mobile-web-app-capable`,
    `apple-mobile-web-app-status-bar-style`, `apple-touch-icon`.
  - CSP header set via Cloudflare Pages `_headers`:
    `default-src 'self'; img-src 'self' https://assets.aiplottwist.example
    data: blob:; media-src 'self' https://assets.aiplottwist.example; style-
    src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'
    https://api.aiplottwist.example;`.
- **FR-014**: Build artifact constraints:
  - Main route bundle (gzipped) ≤ 80 KB.
  - First-route LCP image is the chapter panel 1; the rest are lazy-loaded.
  - Fonts: system stack (no web fonts in MVP) — avoids extra requests.
- **FR-015**: `.github/workflows/lighthouse-ci.yml` runs on every PR against
  the Cloudflare Pages preview URL. Fails the PR on any budget miss
  (scores or per-route).

### Non-Functional Requirements

- **NFR-001**: First Contentful Paint ≤ 2 s on Slow 3G (Lighthouse).
- **NFR-002**: Time to Interactive ≤ 3.5 s on Slow 3G.
- **NFR-003**: Cold install from PWA tap on home screen → first paint ≤ 1.5 s.
- **NFR-004**: SW precache size ≤ 200 KB gz.
- **NFR-005**: `/internal/client-log` p95 < 50 ms (it's a no-op log).

### Out of Scope (for this feature)

- Profile editing (display name change). Deferred.
- Dark mode toggle (we honor `prefers-color-scheme`).
- Multi-language. Spanish only.
- Animation library (Lottie etc.). System-level transitions only.
- Real notification subscription. Module 011.
- A11y audit by a third party. Lighthouse + manual smoke is the MVP bar.
- Sentry / Datadog. Custom client-log is the MVP surface.
