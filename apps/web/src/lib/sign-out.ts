/**
 * Sign-out helper — clears auth + dispatches the global logout event.
 *
 * Module 010 / Task T-007.
 *
 * Extracted from ``settings.svelte`` so:
 *   - Other surfaces (e.g. the API interceptor on 401) can reuse it.
 *   - It can be unit-tested without rendering the Settings component.
 *
 * The auth store's :func:`clear` already drops the JWT + device secret
 * from IndexedDB. We dispatch ``auth:logout`` so :file:`App.svelte`'s
 * listener navigates back to ``/onboarding`` — same path the API
 * interceptor uses when the server forces a token expiry.
 */

import { authStore } from './auth-store.svelte';

export async function signOut(): Promise<void> {
  await authStore.clear();
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event('auth:logout'));
  }
}
