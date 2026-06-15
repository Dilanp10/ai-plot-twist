/**
 * Push notification store — Svelte 5 universal reactivity.
 *
 * Module 011 / Task T-011.
 *
 * Manages the full lifecycle of a Web Push subscription:
 *   init()    — read current browser state, restore serverKnowsAboutMe
 *               from localStorage if a subscription already exists.
 *   enable()  — request permission → get VAPID key → pushManager.subscribe
 *               → POST /push/subscribe.
 *   disable() — pushManager.unsubscribe → DELETE /push/subscriptions/{id}.
 *
 * The server-side subscription id is persisted in localStorage under
 * ``push_sub_id`` so ``disable()`` can call the DELETE endpoint across
 * page reloads without needing a round-trip to the server.
 *
 * Guard: both init() and enable() are no-ops when the browser lacks the
 * Notification or serviceWorker APIs (e.g. non-HTTPS origins, Safari <16,
 * server-side rendering).
 */

import { getPushPublicKey, subscribePush, unsubscribePush } from './push-api';

// ---------------------------------------------------------------------------
// localStorage key for the server-side subscription id
// ---------------------------------------------------------------------------

const STORAGE_KEY = 'push_sub_id';

function _getStoredSubId(): number | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const n = parseInt(raw, 10);
    return isNaN(n) ? null : n;
  } catch {
    return null;
  }
}

function _setStoredSubId(id: number): void {
  try {
    localStorage.setItem(STORAGE_KEY, String(id));
  } catch {
    // storage quota exceeded or private-mode block — best effort
  }
}

function _clearStoredSubId(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}

// ---------------------------------------------------------------------------
// VAPID key conversion
// ---------------------------------------------------------------------------

function _urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding)
    .replace(/-/g, '+')
    .replace(/_/g, '/');
  const rawData = atob(base64);
  return Uint8Array.from([...rawData].map((c) => c.charCodeAt(0)));
}

// ---------------------------------------------------------------------------
// Module-level reactive state
// ---------------------------------------------------------------------------

let _permission = $state<NotificationPermission>('default');
let _subscription = $state<PushSubscription | null>(null);
let _serverKnowsAboutMe = $state<boolean>(false);

// ---------------------------------------------------------------------------
// Public store
// ---------------------------------------------------------------------------

export const pushStore = {
  get permission(): NotificationPermission {
    return _permission;
  },
  get subscription(): PushSubscription | null {
    return _subscription;
  },
  get serverKnowsAboutMe(): boolean {
    return _serverKnowsAboutMe;
  },

  /**
   * Read the current browser notification state and restore the
   * subscription from the service worker's push manager.
   *
   * Call once on app boot (e.g. in AppShell.svelte's onMount).
   */
  async init(): Promise<void> {
    if (!('Notification' in window) || !('serviceWorker' in navigator)) return;

    _permission = Notification.permission;
    if (_permission !== 'granted') return;

    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    _subscription = sub;

    if (sub !== null && _getStoredSubId() !== null) {
      _serverKnowsAboutMe = true;
    }
  },

  /**
   * Request notification permission, subscribe via the service worker's
   * push manager, and register the subscription with the server.
   *
   * No-op when the browser doesn't support the required APIs or the
   * server returns an error fetching the VAPID public key.
   */
  async enable(): Promise<void> {
    if (!('Notification' in window) || !('serviceWorker' in navigator)) return;

    const permission = await Notification.requestPermission();
    _permission = permission;
    if (permission !== 'granted') return;

    // Fetch VAPID public key from server
    const keyResult = await getPushPublicKey();
    if (!keyResult.ok) return;

    const applicationServerKey = _urlBase64ToUint8Array(
      keyResult.data.public_key,
    );

    // Subscribe via browser push manager
    const reg = await navigator.serviceWorker.ready;
    let sub: PushSubscription;
    try {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey,
      });
    } catch {
      return;
    }
    _subscription = sub;

    // Extract encryption keys from the subscription
    const json = sub.toJSON() as {
      endpoint: string;
      keys?: { p256dh?: string; auth?: string };
    };
    if (!json.keys?.p256dh || !json.keys?.auth) return;

    // Register with the server
    const result = await subscribePush({
      endpoint: json.endpoint,
      p256dh: json.keys.p256dh,
      auth: json.keys.auth,
      user_agent: navigator.userAgent,
    });
    if (!result.ok) return;

    _setStoredSubId(result.data.id);
    _serverKnowsAboutMe = true;
  },

  /**
   * Unsubscribe from the browser push manager and remove the subscription
   * from the server.
   *
   * Always clears local state, even if the server DELETE fails — the server
   * will clean up stale subscriptions via the fan-out's 410 Gone path.
   */
  async disable(): Promise<void> {
    const sub = _subscription;
    if (!sub) return;

    const subId = _getStoredSubId();

    // Unsubscribe from browser first so the state is cleared even if the
    // server call fails.
    try {
      await sub.unsubscribe();
    } finally {
      _subscription = null;
      _serverKnowsAboutMe = false;
      _clearStoredSubId();
    }

    if (subId !== null) {
      // Best-effort server delete — failure is non-fatal (server 410 path cleans up).
      await unsubscribePush(subId);
    }
  },

  /** Test helper — reset module state between tests. */
  _reset(): void {
    _permission = 'default';
    _subscription = null;
    _serverKnowsAboutMe = false;
    _clearStoredSubId();
  },
};
