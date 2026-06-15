/**
 * Unit tests for push-store (T-011).
 *
 * Mocks push-api so we exercise the store flows without hitting fetch.
 * Stubs Notification and navigator.serviceWorker since JSDOM doesn't
 * ship those APIs.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { pushStore } from '../src/lib/push-store.svelte';

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

const { mockGetKey, mockSubscribe, mockUnsubscribe } = vi.hoisted(() => ({
  mockGetKey: vi.fn(),
  mockSubscribe: vi.fn(),
  mockUnsubscribe: vi.fn(),
}));

vi.mock('../src/lib/push-api', () => ({
  getPushPublicKey: mockGetKey,
  subscribePush: mockSubscribe,
  unsubscribePush: mockUnsubscribe,
}));

// ---------------------------------------------------------------------------
// Browser API stubs
// ---------------------------------------------------------------------------

const mockGetSubscription = vi.fn();
const mockPushSubscribe = vi.fn();

const mockPushManager = {
  getSubscription: mockGetSubscription,
  subscribe: mockPushSubscribe,
};

const mockReg = { pushManager: mockPushManager };

function _stubNotification(permission: NotificationPermission): void {
  vi.stubGlobal('Notification', {
    permission,
    requestPermission: vi.fn().mockResolvedValue(permission),
  });
}

function _stubServiceWorker(): void {
  Object.defineProperty(navigator, 'serviceWorker', {
    writable: true,
    configurable: true,
    value: { ready: Promise.resolve(mockReg) },
  });
}

/** Minimal PushSubscription-like object the store calls .toJSON() and .unsubscribe() on. */
function _makeSub(overrides: Partial<{ endpoint: string; p256dh: string; auth: string }> = {}) {
  const endpoint = overrides.endpoint ?? 'https://push.example/endpoint-abc';
  const p256dh = overrides.p256dh ?? 'pkvalue';
  const auth = overrides.auth ?? 'authvalue';
  return {
    endpoint,
    toJSON: () => ({ endpoint, keys: { p256dh, auth } }),
    unsubscribe: vi.fn().mockResolvedValue(true),
  } as unknown as PushSubscription;
}

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(() => {
  pushStore._reset();
  vi.clearAllMocks();
  _stubServiceWorker();
  mockGetSubscription.mockResolvedValue(null);
  mockGetKey.mockResolvedValue({ ok: true, data: { public_key: 'BFakePubKey' } });
  mockSubscribe.mockResolvedValue({ ok: true, data: { id: 99 } });
  mockUnsubscribe.mockResolvedValue({ ok: true, data: null });
  // Stub atob for VAPID key conversion (JSDOM has atob)
  // localStorage is available in JSDOM
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// init()
// ---------------------------------------------------------------------------

describe('pushStore.init', () => {
  it('sets permission from Notification.permission', async () => {
    _stubNotification('denied');
    await pushStore.init();
    expect(pushStore.permission).toBe('denied');
  });

  it('does not call getSubscription when permission is not granted', async () => {
    _stubNotification('denied');
    await pushStore.init();
    expect(mockGetSubscription).not.toHaveBeenCalled();
    expect(pushStore.subscription).toBeNull();
  });

  it('loads existing subscription when permission is granted', async () => {
    _stubNotification('granted');
    const sub = _makeSub();
    mockGetSubscription.mockResolvedValue(sub);

    await pushStore.init();

    // $state wraps in a Proxy — use endpoint to assert identity
    expect(pushStore.subscription).not.toBeNull();
    expect(pushStore.subscription?.endpoint).toBe('https://push.example/endpoint-abc');
  });

  it('sets serverKnowsAboutMe=true when sub exists and subId is in localStorage', async () => {
    _stubNotification('granted');
    const sub = _makeSub();
    mockGetSubscription.mockResolvedValue(sub);
    localStorage.setItem('push_sub_id', '42');

    await pushStore.init();

    expect(pushStore.serverKnowsAboutMe).toBe(true);
  });

  it('leaves serverKnowsAboutMe=false when subId is missing from localStorage', async () => {
    _stubNotification('granted');
    mockGetSubscription.mockResolvedValue(_makeSub());

    await pushStore.init();

    expect(pushStore.serverKnowsAboutMe).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// enable()
// ---------------------------------------------------------------------------

describe('pushStore.enable', () => {
  it('returns early and does not call APIs when permission is denied', async () => {
    vi.stubGlobal('Notification', {
      permission: 'default',
      requestPermission: vi.fn().mockResolvedValue('denied'),
    });

    await pushStore.enable();

    expect(mockGetKey).not.toHaveBeenCalled();
    expect(mockPushSubscribe).not.toHaveBeenCalled();
    expect(pushStore.permission).toBe('denied');
    expect(pushStore.serverKnowsAboutMe).toBe(false);
  });

  it('subscribes via pushManager and registers with server on granted permission', async () => {
    vi.stubGlobal('Notification', {
      permission: 'default',
      requestPermission: vi.fn().mockResolvedValue('granted'),
    });
    const sub = _makeSub();
    mockPushSubscribe.mockResolvedValue(sub);

    await pushStore.enable();

    expect(mockGetKey).toHaveBeenCalledOnce();
    expect(mockPushSubscribe).toHaveBeenCalledOnce();
    expect(mockSubscribe).toHaveBeenCalledOnce();

    const [body] = mockSubscribe.mock.calls[0] as [{ endpoint: string; p256dh: string; auth: string }];
    expect(body.endpoint).toBe('https://push.example/endpoint-abc');
    expect(body.p256dh).toBe('pkvalue');
    expect(body.auth).toBe('authvalue');
  });

  it('sets serverKnowsAboutMe=true and persists subId after successful enable', async () => {
    vi.stubGlobal('Notification', {
      permission: 'default',
      requestPermission: vi.fn().mockResolvedValue('granted'),
    });
    mockPushSubscribe.mockResolvedValue(_makeSub());
    mockSubscribe.mockResolvedValue({ ok: true, data: { id: 77 } });

    await pushStore.enable();

    expect(pushStore.serverKnowsAboutMe).toBe(true);
    expect(localStorage.getItem('push_sub_id')).toBe('77');
  });

  it('does not set serverKnowsAboutMe when server POST fails', async () => {
    vi.stubGlobal('Notification', {
      permission: 'default',
      requestPermission: vi.fn().mockResolvedValue('granted'),
    });
    mockPushSubscribe.mockResolvedValue(_makeSub());
    mockSubscribe.mockResolvedValue({ ok: false, status: 503, body: null });

    await pushStore.enable();

    expect(pushStore.serverKnowsAboutMe).toBe(false);
  });

  it('returns early when VAPID key fetch fails', async () => {
    vi.stubGlobal('Notification', {
      permission: 'default',
      requestPermission: vi.fn().mockResolvedValue('granted'),
    });
    mockGetKey.mockResolvedValue({ ok: false, status: 503, body: null });

    await pushStore.enable();

    expect(mockPushSubscribe).not.toHaveBeenCalled();
    expect(pushStore.serverKnowsAboutMe).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// disable()
// ---------------------------------------------------------------------------

describe('pushStore.disable', () => {
  it('is a no-op when there is no active subscription', async () => {
    await pushStore.disable();
    expect(mockUnsubscribe).not.toHaveBeenCalled();
  });

  it('calls browser unsubscribe and clears local state', async () => {
    // Seed state as if enable() already ran
    vi.stubGlobal('Notification', {
      permission: 'default',
      requestPermission: vi.fn().mockResolvedValue('granted'),
    });
    const sub = _makeSub();
    mockPushSubscribe.mockResolvedValue(sub);
    mockSubscribe.mockResolvedValue({ ok: true, data: { id: 55 } });
    await pushStore.enable();

    expect(pushStore.serverKnowsAboutMe).toBe(true);

    await pushStore.disable();

    expect(sub.unsubscribe).toHaveBeenCalledOnce();
    expect(pushStore.subscription).toBeNull();
    expect(pushStore.serverKnowsAboutMe).toBe(false);
    expect(localStorage.getItem('push_sub_id')).toBeNull();
  });

  it('calls DELETE on server with stored subId', async () => {
    vi.stubGlobal('Notification', {
      permission: 'default',
      requestPermission: vi.fn().mockResolvedValue('granted'),
    });
    mockPushSubscribe.mockResolvedValue(_makeSub());
    mockSubscribe.mockResolvedValue({ ok: true, data: { id: 55 } });
    await pushStore.enable();

    await pushStore.disable();

    expect(mockUnsubscribe).toHaveBeenCalledWith(55);
  });

  it('clears local state even if server DELETE fails', async () => {
    vi.stubGlobal('Notification', {
      permission: 'default',
      requestPermission: vi.fn().mockResolvedValue('granted'),
    });
    mockPushSubscribe.mockResolvedValue(_makeSub());
    mockSubscribe.mockResolvedValue({ ok: true, data: { id: 55 } });
    await pushStore.enable();

    mockUnsubscribe.mockResolvedValue({ ok: false, status: 404, body: null });

    await pushStore.disable();

    expect(pushStore.subscription).toBeNull();
    expect(pushStore.serverKnowsAboutMe).toBe(false);
  });
});
