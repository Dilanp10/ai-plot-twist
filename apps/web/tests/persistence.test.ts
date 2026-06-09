/**
 * Unit tests: IndexedDB persistence helpers.
 *
 * Module 002 / Task T-018.
 *
 * Uses fake-indexeddb to patch global.indexedDB so the same code that runs
 * in the browser works inside vitest / jsdom without a real browser.
 */

import 'fake-indexeddb/auto';
import { beforeEach, describe, expect, it } from 'vitest';
import { _resetDB, clearAuth, getAuth, setAuth } from '../src/lib/persistence';

// Reset the cached DB connection + the fake IndexedDB before each test so
// every test starts with a clean slate.
beforeEach(() => {
  _resetDB();
});

describe('getAuth', () => {
  it('returns empty object when nothing is stored', async () => {
    const data = await getAuth();
    expect(data.jwt).toBeUndefined();
    expect(data.deviceSecret).toBeUndefined();
  });
});

describe('setAuth + getAuth roundtrip', () => {
  it('persists and retrieves both fields', async () => {
    await setAuth({ jwt: 'my.jwt.token', deviceSecret: 'my-device-secret' });
    const data = await getAuth();
    expect(data.jwt).toBe('my.jwt.token');
    expect(data.deviceSecret).toBe('my-device-secret');
  });

  it('overwrites existing values on second setAuth call', async () => {
    await setAuth({ jwt: 'old.jwt', deviceSecret: 'old-secret' });
    await setAuth({ jwt: 'new.jwt', deviceSecret: 'new-secret' });
    const data = await getAuth();
    expect(data.jwt).toBe('new.jwt');
    expect(data.deviceSecret).toBe('new-secret');
  });
});

describe('clearAuth', () => {
  it('removes both fields after setAuth', async () => {
    await setAuth({ jwt: 'some.jwt', deviceSecret: 'some-secret' });
    await clearAuth();
    const data = await getAuth();
    expect(data.jwt).toBeUndefined();
    expect(data.deviceSecret).toBeUndefined();
  });

  it('is idempotent on an empty store', async () => {
    await expect(clearAuth()).resolves.toBeUndefined();
  });
});
