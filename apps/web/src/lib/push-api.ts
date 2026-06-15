/**
 * Typed client wrappers for the module 011 Web Push endpoints.
 *
 * Module 011 / Task T-011.
 *
 * Three endpoints:
 *   GET  /push/public-key         — unauthenticated (bare fetch)
 *   POST /push/subscribe          — authenticated (apiFetch)
 *   DELETE /push/subscriptions/N  — authenticated (bare fetch, handles 204)
 *
 * The DELETE uses bare fetch + manual JWT attachment instead of apiFetch
 * because apiFetch always calls resp.json() on ok responses, which would
 * throw on a 204 No Content body.
 */

import { apiFetch, type ApiResult } from './api';
import { authStore } from './auth-store.svelte';

// ---------------------------------------------------------------------------
// Response types
// ---------------------------------------------------------------------------

export interface PushPublicKeyResponse {
  public_key: string;
}

export interface SubscribeBody {
  endpoint: string;
  p256dh: string;
  auth: string;
  user_agent?: string;
}

export interface SubscribeResponse {
  id: number;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

const BASE = '/api/v1/push';

/**
 * GET /api/v1/push/public-key
 *
 * Returns the server's VAPID public key.  No auth required.
 * 503 → push_not_configured.
 */
export async function getPushPublicKey(): Promise<
  ApiResult<PushPublicKeyResponse>
> {
  try {
    const resp = await fetch(`${BASE}/public-key`);
    if (resp.ok) {
      const data = (await resp.json()) as PushPublicKeyResponse;
      return { ok: true, data };
    }
    const body: unknown = await resp.json().catch(() => null);
    return { ok: false, status: resp.status, body };
  } catch {
    return { ok: false, status: 0, body: null };
  }
}

/**
 * POST /api/v1/push/subscribe
 *
 * Upserts a push subscription for the authenticated user.
 * Idempotent on endpoint reuse (re-subscribing resets failure_count).
 *
 * 201 → SubscribeResponse  { id: number }
 * 401 → unauthenticated
 */
export async function subscribePush(
  body: SubscribeBody,
): Promise<ApiResult<SubscribeResponse>> {
  return apiFetch<SubscribeResponse>(`${BASE}/subscribe`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/**
 * DELETE /api/v1/push/subscriptions/{id}
 *
 * Hard-deletes a subscription owned by the authenticated user.
 *
 * 204 → success (no body)
 * 404 → subscription_not_found
 * 401 → unauthenticated
 */
export async function unsubscribePush(id: number): Promise<ApiResult<null>> {
  const headers: Record<string, string> = {};
  if (authStore.jwt) {
    headers['Authorization'] = `Bearer ${authStore.jwt}`;
  }
  try {
    const resp = await fetch(`${BASE}/subscriptions/${id}`, {
      method: 'DELETE',
      headers,
    });
    if (resp.status === 204) return { ok: true, data: null };
    const body: unknown = await resp.json().catch(() => null);
    return { ok: false, status: resp.status, body };
  } catch {
    return { ok: false, status: 0, body: null };
  }
}
