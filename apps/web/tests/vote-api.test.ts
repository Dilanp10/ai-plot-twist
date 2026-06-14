/**
 * Unit tests for the vote-api typed client wrappers (T-009).
 *
 * Mocks ``apiFetch`` so we only assert the wire shape the wrappers send:
 * URL (incl. query string), HTTP method, headers, and JSON body.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { castVote, getVoteFeed } from '../src/lib/vote-api';

const { mockApiFetch } = vi.hoisted(() => ({ mockApiFetch: vi.fn() }));
vi.mock('../src/lib/api', () => ({ apiFetch: mockApiFetch }));

beforeEach(() => {
  mockApiFetch.mockResolvedValue({ ok: true, data: {} });
});

afterEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// getVoteFeed
// ---------------------------------------------------------------------------

describe('getVoteFeed', () => {
  it('GETs /vote-feed with no query string when called with no args', async () => {
    await getVoteFeed();
    const [url, init] = mockApiFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/twists/vote-feed');
    expect(init.method).toBe('GET');
  });

  it('serializes sort, limit, and cursor as query params', async () => {
    await getVoteFeed({ sort: 'hot', limit: 10, cursor: 'abc123' });
    const [url] = mockApiFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      '/api/v1/twists/vote-feed?sort=hot&limit=10&cursor=abc123',
    );
  });

  it('omits null cursor from the query string', async () => {
    await getVoteFeed({ sort: 'recent', cursor: null });
    const [url] = mockApiFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/twists/vote-feed?sort=recent');
  });

  it('serializes limit=0 explicitly (so 0 is not lost)', async () => {
    await getVoteFeed({ limit: 0 });
    const [url] = mockApiFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/twists/vote-feed?limit=0');
  });

  it('URL-encodes a cursor with non-ASCII or special chars', async () => {
    // Cursors should be base64-urlsafe so this shouldn't happen in practice,
    // but the wrapper must still encode reserved chars.
    await getVoteFeed({ cursor: 'a b&c' });
    const [url] = mockApiFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/twists/vote-feed?cursor=a+b%26c');
  });
});

// ---------------------------------------------------------------------------
// castVote
// ---------------------------------------------------------------------------

describe('castVote', () => {
  it('POSTs to /api/v1/twists/vote with JSON body', async () => {
    await castVote('11111111-1111-1111-1111-111111111111');
    const [url, init] = mockApiFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/twists/vote');
    expect(init.method).toBe('POST');

    const headers = init.headers as Record<string, string>;
    expect(headers['Content-Type']).toBe('application/json');

    expect(init.body).toBe(
      JSON.stringify({ twist_id: '11111111-1111-1111-1111-111111111111' }),
    );
  });
});
