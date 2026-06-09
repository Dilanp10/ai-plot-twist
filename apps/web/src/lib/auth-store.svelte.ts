/**
 * Auth store — Svelte 5 universal reactivity.
 *
 * Module 002 / Task T-019.
 *
 * Uses Svelte 5 `$state` runes (requires `.svelte.ts` extension so the
 * Svelte compiler processes the file). The state is module-level, so it
 * is shared across all importers — a true singleton per page load.
 *
 * API:
 *   authStore.jwt          – current JWT string, or null
 *   authStore.user         – current PublicUser, or null
 *   authStore.init()       – load jwt from IndexedDB on app boot
 *   authStore.setSession() – persist + update state after login/refresh
 *   authStore.clear()      – clear state + IndexedDB on logout
 *
 * Note: `user` is NOT persisted in IndexedDB (the JWT is sufficient to
 * re-fetch the user on the next app boot via GET /auth/me).
 */

import { clearAuth, getAuth, setAuth } from './persistence';

export interface PublicUser {
  public_id: string;
  display_name: string;
  created_at: string;
  last_seen_at: string;
}

// ---------------------------------------------------------------------------
// Module-level reactive state (Svelte 5 rune)
// ---------------------------------------------------------------------------

let _jwt = $state<string | null>(null);
let _user = $state<PublicUser | null>(null);

// ---------------------------------------------------------------------------
// Store object
// ---------------------------------------------------------------------------

export const authStore = {
  /** Current JWT, or null when unauthenticated. */
  get jwt(): string | null {
    return _jwt;
  },

  /** Current user profile, or null when unauthenticated. */
  get user(): PublicUser | null {
    return _user;
  },

  /**
   * Load the JWT from IndexedDB.  Call once on app boot before routing.
   * Does NOT fetch user details — the router decides whether to call
   * GET /auth/me and populate `user`.
   */
  async init(): Promise<void> {
    const stored = await getAuth();
    _jwt = stored.jwt ?? null;
  },

  /**
   * Persist a new session after a successful login or token refresh.
   *
   * @param jwt          The new JWT string.
   * @param deviceSecret The raw device secret (base64url, 43 chars).
   * @param user         The user profile returned by the server.
   */
  async setSession(
    jwt: string,
    deviceSecret: string,
    user: PublicUser,
  ): Promise<void> {
    await setAuth({ jwt, deviceSecret });
    _jwt = jwt;
    _user = user;
  },

  /** Clear all auth state and remove credentials from IndexedDB. */
  async clear(): Promise<void> {
    await clearAuth();
    _jwt = null;
    _user = null;
  },
};
