/**
 * Minimal hash-based SPA router.
 *
 * Module 002 / Task T-022.
 *
 * Uses Svelte 5 `$state` rune so the current route is reactive — any
 * Svelte component that reads `router.current` will re-render when the
 * route changes.  The `.svelte.ts` extension ensures the Svelte compiler
 * processes `$state` (same convention as `auth-store.svelte.ts`).
 *
 * Usage:
 *   import { router } from './router.svelte';
 *
 *   router.current             // reactive current path, e.g. '/today'
 *   router.navigate('/today')  // programmatic navigation
 */

// ---------------------------------------------------------------------------
// Reactive state
// ---------------------------------------------------------------------------

let _route = $state<string>('/');

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export const router = {
  /** Current route path (e.g. '/today' or '/onboarding').  Reactive. */
  get current(): string {
    return _route;
  },

  /**
   * Navigate to *path*.
   * Updates the reactive state and the URL hash simultaneously.
   */
  navigate(path: string): void {
    _route = path;
    if (typeof window !== 'undefined') {
      window.location.hash = path;
    }
  },

  /**
   * Sync reactive state from `window.location.hash`.
   * Useful when the app boots and the user already has a deep-link hash.
   */
  syncFromHash(): void {
    if (typeof window !== 'undefined') {
      _route = window.location.hash.slice(1) || '/';
    }
  },
};

// ---------------------------------------------------------------------------
// Browser back / forward support
// ---------------------------------------------------------------------------

if (typeof window !== 'undefined') {
  window.addEventListener('hashchange', () => {
    _route = window.location.hash.slice(1) || '/';
  });
}

// ---------------------------------------------------------------------------
// Test helper
// ---------------------------------------------------------------------------

/** Reset to a known route.  Only for tests. */
export function _resetRoute(path = '/'): void {
  _route = path;
}
