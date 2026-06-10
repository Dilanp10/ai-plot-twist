/**
 * Unit tests: chapterStore (Svelte 5 universal reactivity).
 *
 * Module 004 / Task T-012.
 *
 * Mocks apiFetch so no HTTP calls are made.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const { mockApiFetch } = vi.hoisted(() => {
  const mockApiFetch = vi.fn();
  return { mockApiFetch };
});

vi.mock('../src/lib/api', () => ({ apiFetch: mockApiFetch }));

import { chapterStore, type TodayResponse } from '../src/lib/chapter-store.svelte';

const MOCK_PAYLOAD: TodayResponse = {
  cycle_state: 'RECEPCION_IDEAS',
  season: { slug: 's01', title: 'Test Season' },
  chapter: {
    id: '9f3a3b5f-0000-4000-8000-000000007e2c',
    day_index: 7,
    title: 'El día 7',
    synopsis: 'syn',
    released_at: '2026-06-09T15:00:00Z',
    panels: [
      {
        idx: 1,
        image_url: 'https://x/1.webp',
        image_blurhash: null,
        tts_url: null,
        narration: 'narr',
        mood: 'calm',
      },
    ],
    cliffhanger: 'cliff',
  },
  windows: {
    submit_until: '2026-06-09T21:00:00Z',
    vote_from: '2026-06-09T21:00:00Z',
    vote_until: '2026-06-10T02:00:00Z',
    next_release: '2026-06-10T15:00:00Z',
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  chapterStore._reset();
});

// ---------------------------------------------------------------------------
// Initial state
// ---------------------------------------------------------------------------

describe('initial state', () => {
  it('status is idle', () => {
    expect(chapterStore.status).toBe('idle');
  });
  it('data is null', () => {
    expect(chapterStore.data).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// load() — happy path
// ---------------------------------------------------------------------------

describe('load() — ok', () => {
  it('populates data and sets status=ok', async () => {
    mockApiFetch.mockResolvedValueOnce({ ok: true, data: MOCK_PAYLOAD });
    await chapterStore.load();
    expect(chapterStore.status).toBe('ok');
    expect(chapterStore.data).toEqual(MOCK_PAYLOAD);
  });

  it('clears any previous error state on success', async () => {
    mockApiFetch.mockResolvedValueOnce({ ok: false, status: 500, body: { code: 'x' } });
    await chapterStore.load();
    expect(chapterStore.status).toBe('error');

    mockApiFetch.mockResolvedValueOnce({ ok: true, data: MOCK_PAYLOAD });
    await chapterStore.load();
    expect(chapterStore.status).toBe('ok');
    expect(chapterStore.errorBody).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// load() — error mapping
// ---------------------------------------------------------------------------

describe('load() — error mapping', () => {
  it('503 under_maintenance maps to status=maintenance with reason', async () => {
    mockApiFetch.mockResolvedValueOnce({
      ok: false,
      status: 503,
      body: { code: 'under_maintenance', reason: 'ajustando la bible' },
    });
    await chapterStore.load();
    expect(chapterStore.status).toBe('maintenance');
    expect(chapterStore.maintenanceReason).toBe('ajustando la bible');
  });

  it('503 no_active_season maps to status=no_season', async () => {
    mockApiFetch.mockResolvedValueOnce({
      ok: false,
      status: 503,
      body: { code: 'no_active_season' },
    });
    await chapterStore.load();
    expect(chapterStore.status).toBe('no_season');
  });

  it('404 no_live_chapter maps to status=no_release with firstReleaseAt', async () => {
    mockApiFetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      body: { code: 'no_live_chapter', first_release_at: '2026-06-11T15:00:00Z' },
    });
    await chapterStore.load();
    expect(chapterStore.status).toBe('no_release');
    expect(chapterStore.firstReleaseAt).toBe('2026-06-11T15:00:00Z');
  });

  it('unknown error code maps to status=error with body preserved', async () => {
    mockApiFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      body: { code: 'internal_server_error' },
    });
    await chapterStore.load();
    expect(chapterStore.status).toBe('error');
    expect(chapterStore.errorBody).toEqual({ code: 'internal_server_error' });
  });

  it('null body still produces status=error', async () => {
    mockApiFetch.mockResolvedValueOnce({ ok: false, status: 502, body: null });
    await chapterStore.load();
    expect(chapterStore.status).toBe('error');
  });
});

// ---------------------------------------------------------------------------
// load() — transition shape
// ---------------------------------------------------------------------------

describe('load() — transition shape', () => {
  it('sets status=loading before resolving', async () => {
    let resolveFn: (value: { ok: true; data: TodayResponse }) => void = () => {};
    mockApiFetch.mockReturnValueOnce(
      new Promise((res) => {
        resolveFn = res;
      }),
    );

    const promise = chapterStore.load();
    expect(chapterStore.status).toBe('loading');

    resolveFn({ ok: true, data: MOCK_PAYLOAD });
    await promise;
    expect(chapterStore.status).toBe('ok');
  });
});

// ---------------------------------------------------------------------------
// refresh() — SWR semantics
// ---------------------------------------------------------------------------

describe('refresh() — SWR', () => {
  it('keeps previous data while fetching new data', async () => {
    mockApiFetch.mockResolvedValueOnce({ ok: true, data: MOCK_PAYLOAD });
    await chapterStore.load();
    expect(chapterStore.data).toEqual(MOCK_PAYLOAD);

    let resolveFn: (value: { ok: true; data: TodayResponse }) => void = () => {};
    mockApiFetch.mockReturnValueOnce(
      new Promise((res) => {
        resolveFn = res;
      }),
    );
    const refreshPromise = chapterStore.refresh();

    // Mid-flight: status STAYS ok and data is preserved.
    expect(chapterStore.status).toBe('ok');
    expect(chapterStore.data).toEqual(MOCK_PAYLOAD);

    const updated: TodayResponse = {
      ...MOCK_PAYLOAD,
      chapter: { ...MOCK_PAYLOAD.chapter, day_index: 8 },
    };
    resolveFn({ ok: true, data: updated });
    await refreshPromise;
    expect(chapterStore.data?.chapter.day_index).toBe(8);
  });

  it('switches to error status if refresh fails', async () => {
    mockApiFetch.mockResolvedValueOnce({ ok: true, data: MOCK_PAYLOAD });
    await chapterStore.load();

    mockApiFetch.mockResolvedValueOnce({
      ok: false,
      status: 503,
      body: { code: 'under_maintenance', reason: 'brb' },
    });
    await chapterStore.refresh();
    expect(chapterStore.status).toBe('maintenance');
    expect(chapterStore.maintenanceReason).toBe('brb');
  });
});
