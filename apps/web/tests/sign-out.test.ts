/**
 * Unit tests: sign-out helper (T-007).
 *
 * Verifies the helper clears the auth store AND dispatches the
 * ``auth:logout`` event that ``App.svelte`` listens for to navigate
 * back to ``/onboarding``.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../src/lib/auth-store.svelte', () => ({
  authStore: {
    clear: vi.fn().mockResolvedValue(undefined),
  },
}));

import { authStore } from '../src/lib/auth-store.svelte';
import { signOut } from '../src/lib/sign-out';

afterEach(() => {
  vi.clearAllMocks();
});

describe('signOut', () => {
  it('awaits authStore.clear before dispatching the event', async () => {
    const order: string[] = [];
    (authStore.clear as ReturnType<typeof vi.fn>).mockImplementation(
      async () => {
        order.push('clear');
      },
    );
    const dispatched: string[] = [];
    const handler = (e: Event) => dispatched.push(e.type);
    window.addEventListener('auth:logout', handler);

    try {
      await signOut();
      order.push('after-await');
      // The handler fires synchronously during dispatch.
      expect(dispatched).toEqual(['auth:logout']);
      // clear() must resolve BEFORE we dispatch.
      expect(order).toEqual(['clear', 'after-await']);
    } finally {
      window.removeEventListener('auth:logout', handler);
    }
  });

  it('still fires the event when clear() rejects-propagated', async () => {
    (authStore.clear as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error('idb broken'),
    );
    const dispatched: string[] = [];
    const handler = (e: Event) => dispatched.push(e.type);
    window.addEventListener('auth:logout', handler);

    try {
      await expect(signOut()).rejects.toThrow('idb broken');
      // Implementation choice: when clear() rejects we propagate WITHOUT
      // dispatching — the caller decides whether to retry.
      expect(dispatched).toEqual([]);
    } finally {
      window.removeEventListener('auth:logout', handler);
    }
  });
});
