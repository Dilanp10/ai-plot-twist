/**
 * Route resolver — pick the initial route from cycle_state + kill-switch.
 *
 * Module 010 / Task T-002.
 *
 * The PWA mounts a route based on the cached or fresh /chapters/today
 * response (FR-002). This module is the single source of truth for that
 * mapping so the App shell, the SW update notifier, and the chapter
 * store all agree.
 *
 * The "show within /today" cycle states (FILTERING, GENERACION,
 * PENDING_RELEASE) intentionally route to ``today`` — the today screen
 * itself decides whether to render the chapter or a waiting placeholder
 * based on the same ``cycle_state``. Same goes for ``FAILED`` and the
 * kill-switch: they map to ``today``, which switches to its maintenance
 * variant via the chapter store's ``status === 'maintenance'`` path.
 *
 * Note: ``today`` is the safe fallback when the cycle state is missing
 * (chapter store still in ``loading``). Callers that have a JWT but no
 * cycle state yet should pass ``cycleState = null`` to land on today
 * and let the loading skeleton handle the rest.
 *
 * Usage:
 *   import { pickRoute } from '$lib/route-resolver';
 *   const route = pickRoute(chapterStore.cycleState, chapterStore.killSwitch);
 *   router.navigate(`/${route}`);
 */

import type { CycleState } from './chapter-store.svelte';

export type RouteName =
  | 'onboarding'
  | 'today'
  | 'vote'
  | 'me'
  | 'settings';

export interface KillSwitchInfo {
  /** Whether the server has flipped the kill switch on. */
  on: boolean;
}

/**
 * Resolve the route to mount given the current cycle state.
 *
 * @param cycleState - The latest cycle_state from /chapters/today; pass
 *   ``null`` when not yet known (still loading) — defaults to today.
 * @param killSwitch - Optional kill-switch info. Any ``on === true``
 *   value forces ``today`` (the maintenance screen lives inside today).
 */
export function pickRoute(
  cycleState: CycleState | null,
  killSwitch?: KillSwitchInfo,
): RouteName {
  if (killSwitch?.on) {
    return 'today';
  }
  if (cycleState === null) {
    return 'today';
  }

  switch (cycleState) {
    case 'VOTACION':
      return 'vote';
    case 'ESTRENO':
    case 'RECEPCION_IDEAS':
    case 'FILTERING':
    case 'GENERACION':
    case 'PENDING_RELEASE':
    case 'FAILED':
      return 'today';
  }
}

/**
 * Whether a given route should be reachable without a JWT.
 *
 * Only ``onboarding`` is public — every other route renders authed
 * content and triggers an ``auth:logout`` redirect when the JWT is
 * absent. Callers can use this to gate ``router.navigate`` calls before
 * issuing them.
 */
export function isPublicRoute(route: RouteName): boolean {
  return route === 'onboarding';
}
