/**
 * Unit tests for the service worker push + notificationclick handlers.
 *
 * Module 011 / T-013.
 *
 * Since we can't import the real SW (workbox imports break in JSDOM),
 * we extract the handler logic and test it against a minimal
 * ServiceWorkerGlobalScope stub.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// ---------------------------------------------------------------------------
// Minimal SW global stub
// ---------------------------------------------------------------------------

type EventHandler = (event: unknown) => void;

const listeners: Record<string, EventHandler> = {};
const mockShowNotification = vi.fn().mockResolvedValue(undefined);
const mockMatchAll = vi.fn().mockResolvedValue([]);
const mockOpenWindow = vi.fn().mockResolvedValue(null);
const mockFocus = vi.fn().mockResolvedValue(undefined);

function stubSWGlobal(): void {
  const swSelf = {
    __WB_MANIFEST: [],
    addEventListener: (type: string, handler: EventHandler) => {
      listeners[type] = handler;
    },
    registration: {
      showNotification: mockShowNotification,
    },
    clients: {
      matchAll: mockMatchAll,
      openWindow: mockOpenWindow,
    },
    skipWaiting: vi.fn(),
  };
  vi.stubGlobal('self', swSelf);
}

// Stub workbox modules so the import doesn't crash in JSDOM.
vi.mock('workbox-core', () => ({ clientsClaim: vi.fn() }));
vi.mock('workbox-precaching', () => ({
  precacheAndRoute: vi.fn(),
  cleanupOutdatedCaches: vi.fn(),
}));
vi.mock('workbox-routing', () => ({ registerRoute: vi.fn() }));
vi.mock('workbox-strategies', () => ({
  CacheFirst: vi.fn(),
  NetworkOnly: vi.fn(),
  StaleWhileRevalidate: vi.fn(),
}));
vi.mock('workbox-expiration', () => ({ ExpirationPlugin: vi.fn() }));
vi.mock('workbox-cacheable-response', () => ({
  CacheableResponsePlugin: vi.fn(),
}));

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  Object.keys(listeners).forEach((k) => delete listeners[k]);
  stubSWGlobal();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

async function loadSW(): Promise<void> {
  await import('../src/service-worker');
}

// ---------------------------------------------------------------------------
// push event
// ---------------------------------------------------------------------------

describe('push event', () => {
  it('shows a notification with the payload title and body', async () => {
    await loadSW();
    const handler = listeners['push'];
    expect(handler).toBeDefined();

    const waitUntilFn = vi.fn((p: Promise<unknown>) => p);
    handler({
      data: {
        json: () => ({
          title: 'Nuevo capítulo',
          body: 'Mirá qué pasó hoy.',
          url: '/today',
        }),
      },
      waitUntil: waitUntilFn,
    });

    await waitUntilFn.mock.calls[0]![0];

    expect(mockShowNotification).toHaveBeenCalledOnce();
    const [title, options] = mockShowNotification.mock.calls[0] as [
      string,
      NotificationOptions & { data: { url: string } },
    ];
    expect(title).toBe('Nuevo capítulo');
    expect(options.body).toBe('Mirá qué pasó hoy.');
    expect(options.data.url).toBe('/today');
  });

  it('falls back to defaults when payload is empty', async () => {
    await loadSW();
    const handler = listeners['push'];

    const waitUntilFn = vi.fn((p: Promise<unknown>) => p);
    handler({
      data: { json: () => ({}) },
      waitUntil: waitUntilFn,
    });

    await waitUntilFn.mock.calls[0]![0];

    const [title, options] = mockShowNotification.mock.calls[0] as [
      string,
      NotificationOptions & { data: { url: string } },
    ];
    expect(title).toBe('AI Plot Twist');
    expect(options.body).toBe('Hay novedades en la historia.');
    expect(options.data.url).toBe('/');
  });

  it('handles malformed JSON gracefully', async () => {
    await loadSW();
    const handler = listeners['push'];

    const waitUntilFn = vi.fn((p: Promise<unknown>) => p);
    handler({
      data: {
        json: () => {
          throw new Error('bad json');
        },
      },
      waitUntil: waitUntilFn,
    });

    await waitUntilFn.mock.calls[0]![0];

    expect(mockShowNotification).toHaveBeenCalledOnce();
    const [title] = mockShowNotification.mock.calls[0] as [string, unknown];
    expect(title).toBe('AI Plot Twist');
  });
});

// ---------------------------------------------------------------------------
// notificationclick event
// ---------------------------------------------------------------------------

describe('notificationclick event', () => {
  it('focuses an existing window when one matches the target URL', async () => {
    await loadSW();
    const handler = listeners['notificationclick'];
    expect(handler).toBeDefined();

    const mockClient = {
      url: 'https://app.example.com/today',
      focus: mockFocus,
    };
    mockMatchAll.mockResolvedValue([mockClient]);

    const closeFn = vi.fn();
    const waitUntilFn = vi.fn((p: Promise<unknown>) => p);
    handler({
      notification: {
        close: closeFn,
        data: { url: '/today' },
      },
      waitUntil: waitUntilFn,
    });

    await waitUntilFn.mock.calls[0]![0];

    expect(closeFn).toHaveBeenCalledOnce();
    expect(mockFocus).toHaveBeenCalledOnce();
    expect(mockOpenWindow).not.toHaveBeenCalled();
  });

  it('opens a new window when no existing client matches', async () => {
    await loadSW();
    const handler = listeners['notificationclick'];

    mockMatchAll.mockResolvedValue([]);

    const closeFn = vi.fn();
    const waitUntilFn = vi.fn((p: Promise<unknown>) => p);
    handler({
      notification: {
        close: closeFn,
        data: { url: '/vote' },
      },
      waitUntil: waitUntilFn,
    });

    await waitUntilFn.mock.calls[0]![0];

    expect(closeFn).toHaveBeenCalledOnce();
    expect(mockOpenWindow).toHaveBeenCalledWith('/vote');
  });

  it('defaults to "/" when notification data has no URL', async () => {
    await loadSW();
    const handler = listeners['notificationclick'];

    mockMatchAll.mockResolvedValue([]);

    const closeFn = vi.fn();
    const waitUntilFn = vi.fn((p: Promise<unknown>) => p);
    handler({
      notification: {
        close: closeFn,
        data: {},
      },
      waitUntil: waitUntilFn,
    });

    await waitUntilFn.mock.calls[0]![0];

    expect(mockOpenWindow).toHaveBeenCalledWith('/');
  });
});
