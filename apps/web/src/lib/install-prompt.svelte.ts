/**
 * Android install prompt — captures ``beforeinstallprompt`` (T-005).
 *
 * Module 010 / Task T-005.
 *
 * Chrome on Android fires ``beforeinstallprompt`` once the PWA meets
 * installability criteria (FR-004). We:
 *   1. capture + ``preventDefault`` the event so Chrome's mini-bar
 *      stays hidden,
 *   2. expose ``installPrompt.canPrompt`` (reactive) so the in-app
 *      install card decides whether to render,
 *   3. expose ``installPrompt.prompt()`` so the card's CTA triggers
 *      the OS dialog.
 *
 * The event fires only once per page-load; subsequent calls to
 * ``prompt()`` are no-ops by spec, so the store toggles ``canPrompt``
 * back to ``false`` after a prompt attempt.
 *
 * Per FR-004 the card should only appear AFTER the user has viewed
 * at least one chapter — the caller (e.g. ``Today``) gates that with
 * its own state.
 */

// The BeforeInstallPromptEvent type isn't in lib.dom yet; declare the
// shape we actually use.
interface BeforeInstallPromptEvent extends Event {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>;
}

let _stored = $state<BeforeInstallPromptEvent | null>(null);

export const installPrompt = {
  /** True when a deferred prompt is available. Reactive. */
  get canPrompt(): boolean {
    return _stored !== null;
  },

  /** Show the OS install dialog. No-op when no prompt is pending. */
  async prompt(): Promise<'accepted' | 'dismissed' | 'noop'> {
    const evt = _stored;
    if (evt === null) return 'noop';
    _stored = null;
    await evt.prompt();
    const { outcome } = await evt.userChoice;
    return outcome;
  },

  /** Drop the captured event without calling prompt(). */
  dismiss(): void {
    _stored = null;
  },
};

/**
 * Wire ``beforeinstallprompt`` to the store. Idempotent — repeated
 * calls add only one listener.
 */
export function installBeforeInstallPromptListener(): void {
  if (typeof window === 'undefined') return;
  if (_listenerInstalled) return;
  _listenerInstalled = true;
  window.addEventListener('beforeinstallprompt', (e: Event) => {
    e.preventDefault();
    _stored = e as BeforeInstallPromptEvent;
  });
  // Clear the cached event after the user actually installs from the
  // OS dialog (the browser fires ``appinstalled``).
  window.addEventListener('appinstalled', () => {
    _stored = null;
  });
}

let _listenerInstalled = false;

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

/** Reset the captured event. Tests only. */
export function _resetInstallPrompt(): void {
  _stored = null;
}

/** Inject a fake event. Tests only. */
export function _setInstallPrompt(evt: BeforeInstallPromptEvent): void {
  _stored = evt;
}
