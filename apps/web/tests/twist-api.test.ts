/**
 * Unit tests for the twist-api typed client wrappers (T-011).
 *
 * Mocks ``apiFetch`` so we only assert the wire shape the wrappers send:
 *   URL, HTTP method, headers (Idempotency-Key for submit), JSON body.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  deleteTwist,
  freshIdempotencyKey,
  getMyTwists,
  submitTwist,
} from '../src/lib/twist-api';

// ---------------------------------------------------------------------------
// apiFetch mock (hoisted)
// ---------------------------------------------------------------------------

const { mockApiFetch } = vi.hoisted(() => ({ mockApiFetch: vi.fn() }));
vi.mock('../src/lib/api', () => ({ apiFetch: mockApiFetch }));

beforeEach(() => {
  mockApiFetch.mockResolvedValue({ ok: true, data: {} });
});

afterEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// freshIdempotencyKey
// ---------------------------------------------------------------------------

describe('freshIdempotencyKey', () => {
  it('returns a UUID-shaped string', () => {
    const k = freshIdempotencyKey();
    expect(k).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
    );
  });

  it('returns a different key each call', () => {
    const a = freshIdempotencyKey();
    const b = freshIdempotencyKey();
    expect(a).not.toBe(b);
  });
});

// ---------------------------------------------------------------------------
// submitTwist
// ---------------------------------------------------------------------------

describe('submitTwist', () => {
  it('POSTs to /api/v1/twists/submit with JSON body and a fresh Idem-Key', async () => {
    await submitTwist('11111111-1111-1111-1111-111111111111', 'Hola mundo loco');

    expect(mockApiFetch).toHaveBeenCalledTimes(1);
    const [url, init] = mockApiFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/twists/submit');
    expect(init.method).toBe('POST');

    const headers = init.headers as Record<string, string>;
    expect(headers['Content-Type']).toBe('application/json');
    expect(headers['Idempotency-Key']).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
    );

    expect(init.body).toBe(
      JSON.stringify({
        chapter_id: '11111111-1111-1111-1111-111111111111',
        content: 'Hola mundo loco',
      }),
    );
  });

  it('reuses the supplied Idempotency-Key when provided', async () => {
    const key = '22222222-2222-2222-2222-222222222222';
    await submitTwist('aaaa', 'contenido suficiente largo', key);

    const [, init] = mockApiFetch.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers['Idempotency-Key']).toBe(key);
  });
});

// ---------------------------------------------------------------------------
// deleteTwist
// ---------------------------------------------------------------------------

describe('deleteTwist', () => {
  it('DELETEs to /api/v1/twists/{public_id}', async () => {
    await deleteTwist('33333333-3333-3333-3333-333333333333');
    const [url, init] = mockApiFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/twists/33333333-3333-3333-3333-333333333333');
    expect(init.method).toBe('DELETE');
  });
});

// ---------------------------------------------------------------------------
// getMyTwists
// ---------------------------------------------------------------------------

describe('getMyTwists', () => {
  it('GETs /api/v1/me/twists', async () => {
    await getMyTwists();
    const [url, init] = mockApiFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/me/twists');
    expect(init.method).toBe('GET');
  });
});
