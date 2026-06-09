/**
 * Unit tests: authStore (Svelte 5 universal reactivity).
 *
 * Module 002 / Task T-019.
 *
 * Uses fake-indexeddb so persistence calls work without a real browser.
 * Each test resets the DB connection and clears auth state to guarantee
 * isolation.
 */

import 'fake-indexeddb/auto';
import { beforeEach, describe, expect, it } from 'vitest';
import { _resetDB } from '../src/lib/persistence';
import { authStore } from '../src/lib/auth-store.svelte';

const MOCK_USER = {
  public_id: '11111111-1111-1111-1111-111111111111',
  display_name: 'TestUser',
  created_at: '2026-06-08T00:00:00Z',
  last_seen_at: '2026-06-08T00:00:00Z',
};

beforeEach(async () => {
  _resetDB();
  await authStore.clear();
});

// ---------------------------------------------------------------------------
// Initial state
// ---------------------------------------------------------------------------

describe('initial state', () => {
  it('jwt is null before init', () => {
    expect(authStore.jwt).toBeNull();
  });

  it('user is null before init', () => {
    expect(authStore.user).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// init()
// ---------------------------------------------------------------------------

describe('init()', () => {
  it('leaves jwt null when IndexedDB is empty', async () => {
    await authStore.init();
    expect(authStore.jwt).toBeNull();
  });

  it('restores jwt from IndexedDB', async () => {
    // Directly persist via setSession so IndexedDB has data
    await authStore.setSession('my.jwt.token', 'my-device-secret', MOCK_USER);
    // Simulate app restart: clear in-memory state only, keep IndexedDB
    _resetDB();
    // Re-init (auth store module-level state persists in the test process,
    // so we manually reset it via clear() then re-init with fresh DB)
    await authStore.clear();        // clears both memory + IndexedDB
    // Now seed only IndexedDB (bypass authStore to simulate stored token)
    const { setAuth } = await import('../src/lib/persistence');
    await setAuth({ jwt: 'restored.jwt', deviceSecret: 'restored-secret' });

    await authStore.init();

    expect(authStore.jwt).toBe('restored.jwt');
  });
});

// ---------------------------------------------------------------------------
// setSession()
// ---------------------------------------------------------------------------

describe('setSession()', () => {
  it('sets jwt and user in memory', async () => {
    await authStore.setSession('tok.en.here', 'dev-secret', MOCK_USER);
    expect(authStore.jwt).toBe('tok.en.here');
    expect(authStore.user).toEqual(MOCK_USER);
  });

  it('persists credentials in IndexedDB', async () => {
    await authStore.setSession('persisted.jwt', 'persisted-secret', MOCK_USER);

    const { getAuth } = await import('../src/lib/persistence');
    const stored = await getAuth();
    expect(stored.jwt).toBe('persisted.jwt');
    expect(stored.deviceSecret).toBe('persisted-secret');
  });

  it('overwrites previous session', async () => {
    await authStore.setSession('old.jwt', 'old-secret', MOCK_USER);
    const newUser = { ...MOCK_USER, display_name: 'NewName' };
    await authStore.setSession('new.jwt', 'new-secret', newUser);

    expect(authStore.jwt).toBe('new.jwt');
    expect(authStore.user?.display_name).toBe('NewName');
  });
});

// ---------------------------------------------------------------------------
// clear()
// ---------------------------------------------------------------------------

describe('clear()', () => {
  it('sets jwt and user to null', async () => {
    await authStore.setSession('tok', 'sec', MOCK_USER);
    await authStore.clear();
    expect(authStore.jwt).toBeNull();
    expect(authStore.user).toBeNull();
  });

  it('removes credentials from IndexedDB', async () => {
    await authStore.setSession('tok', 'sec', MOCK_USER);
    await authStore.clear();

    const { getAuth } = await import('../src/lib/persistence');
    const stored = await getAuth();
    expect(stored.jwt).toBeUndefined();
    expect(stored.deviceSecret).toBeUndefined();
  });

  it('is safe to call when already cleared', async () => {
    await expect(authStore.clear()).resolves.toBeUndefined();
  });
});
