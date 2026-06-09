/**
 * Typed fetch wrapper with JWT authentication and refresh interceptor.
 *
 * Module 002 / Task T-020.
 *
 * Behavior on 401:
 *   1. Attempts GET /auth/refresh once with the stored device_secret.
 *   2. On success: replays the original request with the new JWT.
 *   3. On failure: clears auth state and dispatches `auth:logout` event.
 *
 * Single-flight guard: if multiple requests receive 401 simultaneously,
 * they all share the same in-flight refresh Promise — only one HTTP call
 * is made.
 */

import { authStore } from './auth-store.svelte';
import { getAuth } from './persistence';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ApiOk<T> = { ok: true; data: T };
export type ApiErr = { ok: false; status: number; body: unknown };
export type ApiResult<T> = ApiOk<T> | ApiErr;

// ---------------------------------------------------------------------------
// Refresh single-flight guard
// ---------------------------------------------------------------------------

let _refreshFlight: Promise<boolean> | null = null;

async function _doRefresh(): Promise<boolean> {
  try {
    const stored = await getAuth();
    if (!stored.deviceSecret) {
      await authStore.clear();
      window.dispatchEvent(new CustomEvent('auth:logout'));
      return false;
    }

    const resp = await fetch('/api/v1/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ device_secret: stored.deviceSecret }),
    });

    if (!resp.ok) {
      await authStore.clear();
      window.dispatchEvent(new CustomEvent('auth:logout'));
      return false;
    }

    const payload = (await resp.json()) as { jwt: string };
    await authStore.updateJwt(payload.jwt);
    return true;
  } catch {
    await authStore.clear();
    window.dispatchEvent(new CustomEvent('auth:logout'));
    return false;
  }
}

function _attemptRefresh(): Promise<boolean> {
  if (!_refreshFlight) {
    _refreshFlight = _doRefresh().finally(() => {
      _refreshFlight = null;
    });
  }
  return _refreshFlight;
}

/** Reset the single-flight state.  Only for tests. */
export function _resetRefreshState(): void {
  _refreshFlight = null;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Typed fetch wrapper.
 *
 * Automatically attaches the JWT as `Authorization: Bearer ...` and
 * handles 401 responses with a single refresh + replay cycle.
 */
export async function apiFetch<T>(
  url: string,
  init: RequestInit = {},
): Promise<ApiResult<T>> {
  const headers = new Headers(init.headers as HeadersInit | undefined);
  if (authStore.jwt) {
    headers.set('Authorization', `Bearer ${authStore.jwt}`);
  }

  let resp = await fetch(url, { ...init, headers });

  // Intercept 401 — try to refresh and replay once
  if (resp.status === 401) {
    const refreshed = await _attemptRefresh();
    if (refreshed && authStore.jwt) {
      headers.set('Authorization', `Bearer ${authStore.jwt}`);
      resp = await fetch(url, { ...init, headers });
    }
  }

  if (resp.ok) {
    const data = (await resp.json()) as T;
    return { ok: true, data };
  }

  const body: unknown = await resp.json().catch(() => null);
  return { ok: false, status: resp.status, body };
}
