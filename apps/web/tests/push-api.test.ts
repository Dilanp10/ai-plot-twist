/**
 * Unit tests for push-api typed client wrappers (T-011).
 *
 * getPushPublicKey — bare fetch (no auth), asserts URL + method.
 * subscribePush    — apiFetch, asserts URL + method + JSON body.
 * unsubscribePush  — bare fetch, asserts URL + method + JWT header + 204 handling.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getPushPublicKey, subscribePush, unsubscribePush } from '../src/lib/push-api';

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

const { mockApiFetch } = vi.hoisted(() => ({ mockApiFetch: vi.fn() }));
vi.mock('../src/lib/api', () => ({ apiFetch: mockApiFetch }));

vi.mock('../src/lib/auth-store.svelte', () => ({
  authStore: { jwt: 'test-jwt-token' },
}));

beforeEach(() => {
  mockApiFetch.mockResolvedValue({ ok: true, data: {} });
  vi.stubGlobal('fetch', vi.fn());
});

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// getPushPublicKey
// ---------------------------------------------------------------------------

describe('getPushPublicKey', () => {
  it('GETs /api/v1/push/public-key with no Authorization header', async () => {
    const mockFetch = vi.mocked(fetch);
    mockFetch.mockResolvedValue(
      new Response(JSON.stringify({ public_key: 'BFake' }), { status: 200 }),
    );

    const result = await getPushPublicKey();

    expect(mockFetch).toHaveBeenCalledOnce();
    const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit | undefined];
    expect(url).toBe('/api/v1/push/public-key');
    expect((init?.headers as Record<string, string> | undefined)?.['Authorization']).toBeUndefined();

    expect(result).toEqual({ ok: true, data: { public_key: 'BFake' } });
  });

  it('returns ok:false on 503', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify({ code: 'push_not_configured' }), {
        status: 503,
      }),
    );

    const result = await getPushPublicKey();

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.status).toBe(503);
    }
  });

  it('returns ok:false with status 0 on network error', async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError('Failed to fetch'));

    const result = await getPushPublicKey();

    expect(result).toEqual({ ok: false, status: 0, body: null });
  });
});

// ---------------------------------------------------------------------------
// subscribePush
// ---------------------------------------------------------------------------

describe('subscribePush', () => {
  it('POSTs to /api/v1/push/subscribe with JSON body via apiFetch', async () => {
    mockApiFetch.mockResolvedValue({ ok: true, data: { id: 42 } });

    const result = await subscribePush({
      endpoint: 'https://push.example/abc',
      p256dh: 'pkvalue',
      auth: 'authvalue',
      user_agent: 'TestBrowser/1.0',
    });

    expect(mockApiFetch).toHaveBeenCalledOnce();
    const [url, init] = mockApiFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/push/subscribe');
    expect(init.method).toBe('POST');
    expect((init.headers as Record<string, string>)['Content-Type']).toBe(
      'application/json',
    );
    expect(JSON.parse(init.body as string)).toEqual({
      endpoint: 'https://push.example/abc',
      p256dh: 'pkvalue',
      auth: 'authvalue',
      user_agent: 'TestBrowser/1.0',
    });

    expect(result).toEqual({ ok: true, data: { id: 42 } });
  });

  it('omits user_agent when not provided', async () => {
    mockApiFetch.mockResolvedValue({ ok: true, data: { id: 1 } });
    await subscribePush({
      endpoint: 'https://push.example/xyz',
      p256dh: 'pk',
      auth: 'ak',
    });
    const [, init] = mockApiFetch.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect('user_agent' in body).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// unsubscribePush
// ---------------------------------------------------------------------------

describe('unsubscribePush', () => {
  it('DELETEs /api/v1/push/subscriptions/{id} with JWT header', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 204 }));

    await unsubscribePush(42);

    expect(fetch).toHaveBeenCalledOnce();
    const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/push/subscriptions/42');
    expect(init.method).toBe('DELETE');
    const headers = init.headers as Record<string, string>;
    expect(headers['Authorization']).toBe('Bearer test-jwt-token');
  });

  it('returns ok:true with data:null on 204', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 204 }));
    const result = await unsubscribePush(7);
    expect(result).toEqual({ ok: true, data: null });
  });

  it('returns ok:false on 404', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify({ code: 'subscription_not_found' }), {
        status: 404,
      }),
    );
    const result = await unsubscribePush(999);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.status).toBe(404);
  });

  it('returns ok:false with status 0 on network error', async () => {
    vi.mocked(fetch).mockRejectedValue(new Error('offline'));
    const result = await unsubscribePush(1);
    expect(result).toEqual({ ok: false, status: 0, body: null });
  });
});
