/**
 * Unit tests: client-logger (T-011).
 *
 * Covers:
 *   - logBoundary builds a sanitized boundary payload + POSTs it.
 *   - logEvent throttles at 10 events / 60s.
 *   - installGlobalHandlers wires window.error + unhandledrejection.
 *   - fetch failures are swallowed.
 *   - long messages / stacks get truncated client-side.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Mock router so logger doesn't pull in real svelte runes.
vi.mock('../src/lib/router.svelte', () => ({
  router: { current: '/today' },
  _resetRoute: vi.fn(),
}));

import {
  _resetThrottle,
  installGlobalHandlers,
  logBoundary,
  logEvent,
} from '../src/lib/client-logger';

const ENDPOINT = '/api/v1/internal/client-log';

beforeEach(() => {
  _resetThrottle();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 202 })));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// logBoundary
// ---------------------------------------------------------------------------

describe('logBoundary', () => {
  it('POSTs a boundary payload with message + stack + route', async () => {
    await logBoundary({ message: 'boom', stack: 'at boom (app.js:1)' });

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(ENDPOINT);
    expect(init.method).toBe('POST');
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' });
    const body = JSON.parse(init.body as string);
    expect(body.event).toBe('boundary');
    expect(body.message).toBe('boom');
    expect(body.stack).toBe('at boom (app.js:1)');
    expect(body.route).toBe('/today');
    expect(typeof body.user_agent).toBe('string');
    expect(typeof body.app_version).toBe('string');
    expect(typeof body.timestamp).toBe('string');
  });

  it('truncates oversize messages and stacks', async () => {
    await logBoundary({ message: 'x'.repeat(1000), stack: 'y'.repeat(3000) });

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string);
    expect(body.message.length).toBe(512);
    expect(body.stack.length).toBe(2048);
  });

  it('swallows fetch failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    await expect(
      logBoundary({ message: 'noop' }),
    ).resolves.toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Throttling
// ---------------------------------------------------------------------------

describe('throttling', () => {
  it('drops events after 10 sends in the rolling window', async () => {
    for (let i = 0; i < 10; i++) {
      await logEvent({
        event: 'error',
        user_agent: 'test',
        app_version: '0.0.0',
        timestamp: new Date().toISOString(),
      });
    }
    // 11th should be dropped.
    await logEvent({
      event: 'error',
      user_agent: 'test',
      app_version: '0.0.0',
      timestamp: new Date().toISOString(),
    });

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    expect(fetchMock).toHaveBeenCalledTimes(10);
  });
});

// ---------------------------------------------------------------------------
// installGlobalHandlers
// ---------------------------------------------------------------------------

describe('installGlobalHandlers', () => {
  it('reports window.error events', async () => {
    installGlobalHandlers();
    const err = new Error('synthetic');
    window.dispatchEvent(
      new ErrorEvent('error', { error: err, message: 'synthetic' }),
    );
    // Give the queued fetch a tick.
    await new Promise((r) => setTimeout(r, 0));

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    expect(fetchMock).toHaveBeenCalled();
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string);
    expect(body.event).toBe('error');
    expect(body.message).toBe('synthetic');
  });

  it('reports unhandled promise rejections', async () => {
    installGlobalHandlers();
    const reason = new Error('rejected');
    // jsdom lacks PromiseRejectionEvent — synthesize one as a plain
    // Event with the same .reason shape our handler reads.
    const evt = new Event('unhandledrejection') as Event & {
      reason: unknown;
      promise: Promise<unknown>;
    };
    evt.reason = reason;
    evt.promise = Promise.resolve();
    window.dispatchEvent(evt);
    await new Promise((r) => setTimeout(r, 0));

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    expect(fetchMock).toHaveBeenCalled();
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string);
    expect(body.event).toBe('unhandledrejection');
    expect(body.message).toBe('rejected');
  });
});
