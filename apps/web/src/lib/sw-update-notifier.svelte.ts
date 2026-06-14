/**
 * Service worker update notifier (T-008).
 *
 * Module 010 / Task T-008.
 *
 * When ``vite-plugin-pwa`` registers the SW (T-004) it surfaces a
 * ``needRefresh`` ref. We bridge that to a tiny reactive store the
 * :class:`SwUpdateToast` component consumes — when a new SW is
 * waiting, the toast prompts a refresh; clicking it activates the new
 * SW and reloads the page (spec §Edge Cases).
 *
 * Why a separate store? T-004 (workbox config) hasn't landed yet —
 * decoupling the notifier from the SW registration lets us ship the
 * UI half now and wire ``registerSW({ onNeedRefresh })`` in the
 * vite-plugin-pwa config later without touching the toast.
 */

let _needRefresh = $state(false);
let _activate: () => Promise<void> = async () => {};

export const swUpdate = {
  /** True when a new SW version is waiting to activate. Reactive. */
  get needRefresh(): boolean {
    return _needRefresh;
  },

  /**
   * Apply the pending SW update and reload the page. No-op when no
   * update is pending.
   */
  async applyAndReload(): Promise<void> {
    if (!_needRefresh) return;
    await _activate();
    if (typeof window !== 'undefined') window.location.reload();
  },

  /** Dismiss without applying — the next cold start will pick it up. */
  dismiss(): void {
    _needRefresh = false;
  },
};

/**
 * Called by ``main.ts`` after the SW registration hook fires. Stores
 * the activate callback so :func:`applyAndReload` can trigger
 * ``skipWaiting()`` via the workbox-managed channel.
 */
export function reportSwUpdateAvailable(
  activate: () => Promise<void>,
): void {
  _activate = activate;
  _needRefresh = true;
}

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

export function _resetSwUpdate(): void {
  _needRefresh = false;
  _activate = async () => {};
}
