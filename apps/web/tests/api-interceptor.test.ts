/**
 * Unit tests: apiFetch refresh interceptor.
 *
 * Module 002 / Task T-020.
 *
 * Mocks fetch, auth-store, and persistence so no real HTTP or IndexedDB
 * is needed.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch, _resetRefreshState } from '../src/lib/api';

// ---------------------------------------------------------------------------
// Mocks — vi.hoisted ensures variables are available when vi.mock() runs
// ---------------------------------------------------------------------------

const { mockAuthStore } = vi.hoisted(() => {
  const mockAuthStore = {
    jwt: 'initial-jwt' as string | null,
    updateJwt: vi.fn<[string], Promise<void>>().mockResolvedValue(undefined),
    clear: vi.fn<[], Promise<void>>().mockResolvedValue(undefined),
  };
  return { mockAuthStore };
});

vi.mock('../src/lib/auth-store.svelte', () => ({ authStore: mockAuthStore }));

vi.mock('../src/lib/persistence', () => ({
  getAuth: vi.fn().mockResolvedValue({ deviceSecret: 'stored-device-secret' }),
}));

// Typed mock-fetch helper
type FetchMock = ReturnType<typeof vi.fn>;
let mockFetch: FetchMock;

beforeEach(() => {
  mockFetch = vi.fn();
  vi.stubGlobal('fetch', mockFetch);
  _resetRefreshState();
  mockAuthStore.jwt = 'initial-jwt';
  mockAuthStore.updateJwt.mockClear();
  mockAuthStore.clear.mockClear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// Happy path
// ---------------------------------------------------------------------------

describe('normal requests', () => {
  it('attaches Authorization header when jwt is set', async () => {
    mockFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ hello: 'world' }), { status: 200 }),
    );

    await apiFetch('/api/v1/some-endpoint');

    const [, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers as HeadersInit);
    expect(headers.get('Authorization')).toBe('Bearer initial-jwt');
  });

  it('returns ok:true with parsed data on 200', async () => {
    mockFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ id: 42 }), { status: 200 }),
    );

    const result = await apiFetch<{ id: number }>('/api/v1/data');

    expect(result.ok).toBe(true);
    if (result.ok) expect(result.data).toEqual({ id: 42 });
  });

  it('returns ok:false with status on non-2xx (non-401)', async () => {
    mockFetch.mockResolvedValueOnce(
      new Response(JSON.stringify({ code: 'not_found' }), { status: 404 }),
    );

    const result = await apiFetch('/api/v1/missing');

    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.status).toBe(404);
  });
});

// ---------------------------------------------------------------------------
// 401 → refresh → replay
// ---------------------------------------------------------------------------

describe('401 interception', () => {
  it('retries with new JWT after successful refresh', async () => {
    // 1st call → 401; refresh call → 200; replay → 200
    mockFetch
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ jwt: 'new-jwt' }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ data: 'secret' }), { status: 200 }),
      );

    // Simulate authStore.jwt being updated after refresh
    mockAuthStore.updateJwt.mockImplementationOnce(async (newJwt) => {
      mockAuthStore.jwt = newJwt;
    });

    const result = await apiFetch<{ data: string }>('/api/v1/protected');

    expect(mockFetch).toHaveBeenCalledTimes(3); // original + refresh + replay
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.data).toEqual({ data: 'secret' });
  });

  it('clears auth and returns error when refresh fails', async () => {
    mockFetch
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 401 })); // refresh also 401

    const result = await apiFetch('/api/v1/protected');

    expect(mockAuthStore.clear).toHaveBeenCalled();
    expect(result.ok).toBe(false);
  });

  it('single-flight: two concurrent 401s share one refresh call', async () => {
    // Both requests → 401
    // One refresh call → 200
    // Both replays → 200
    mockFetch
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ jwt: 'shared-new-jwt' }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ a: 1 }), { status: 200 }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ b: 2 }), { status: 200 }),
      );

    mockAuthStore.updateJwt.mockImplementation(async (newJwt) => {
      mockAuthStore.jwt = newJwt;
    });

    const [r1, r2] = await Promise.all([
      apiFetch<{ a: number }>('/api/v1/endpoint-a'),
      apiFetch<{ b: number }>('/api/v1/endpoint-b'),
    ]);

    // Refresh endpoint called exactly once
    const refreshCalls = (mockFetch.mock.calls as [string, ...unknown[]][])
      .filter(([url]) => url === '/api/v1/auth/refresh');
    expect(refreshCalls.length).toBe(1);

    expect(r1.ok).toBe(true);
    expect(r2.ok).toBe(true);
  });
});
