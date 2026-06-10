/**
 * Unit tests: today.svelte real screen.
 *
 * Module 004 / Task T-014.
 *
 * Mocks the chapter-store so render branches can be driven directly without
 * touching the real reactive runtime or HTTP.
 */

import { cleanup, render, screen } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { TodayResponse } from '../src/lib/chapter-store.svelte';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

interface StoreState {
  status:
    | 'idle'
    | 'loading'
    | 'ok'
    | 'maintenance'
    | 'no_season'
    | 'no_release'
    | 'error';
  data: TodayResponse | null;
  maintenanceReason: string | null;
  firstReleaseAt: string | null;
  errorBody: unknown;
}

const storeState: StoreState = {
  status: 'loading',
  data: null,
  maintenanceReason: null,
  firstReleaseAt: null,
  errorBody: null,
};

vi.mock('../src/lib/chapter-store.svelte', () => ({
  chapterStore: {
    get status() {
      return storeState.status;
    },
    get data() {
      return storeState.data;
    },
    get maintenanceReason() {
      return storeState.maintenanceReason;
    },
    get firstReleaseAt() {
      return storeState.firstReleaseAt;
    },
    get errorBody() {
      return storeState.errorBody;
    },
    load: vi.fn().mockResolvedValue(undefined),
    refresh: vi.fn().mockResolvedValue(undefined),
  },
}));

import Today from '../src/routes/today.svelte';

const OK_PAYLOAD: TodayResponse = {
  cycle_state: 'RECEPCION_IDEAS',
  season: { slug: 's01', title: 'Temporada Test' },
  chapter: {
    id: '9f3a3b5f-0000-4000-8000-000000007e2c',
    day_index: 7,
    title: 'El día del prueba',
    synopsis: 'Una sinopsis de prueba.',
    released_at: '2026-06-09T15:00:00Z',
    panels: [
      {
        idx: 1,
        image_url: 'https://x.test/1.webp',
        image_blurhash: null,
        tts_url: null,
        narration: 'Algo pasaba.',
        mood: 'tense',
      },
    ],
    cliffhanger: 'Y entonces escuchó una voz.',
  },
  windows: {
    submit_until: '2026-06-09T21:00:00Z',
    vote_from: '2026-06-09T21:00:00Z',
    vote_until: '2026-06-10T02:00:00Z',
    next_release: '2026-06-10T15:00:00Z',
  },
};

beforeEach(() => {
  storeState.status = 'loading';
  storeState.data = null;
  storeState.maintenanceReason = null;
  storeState.firstReleaseAt = null;
  storeState.errorBody = null;
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Status branches
// ---------------------------------------------------------------------------

describe('today.svelte render branches', () => {
  it('shows skeleton while loading', () => {
    storeState.status = 'loading';
    render(Today);
    expect(screen.getByTestId('loading')).toBeTruthy();
  });

  it('renders the chapter when status=ok', () => {
    storeState.status = 'ok';
    storeState.data = OK_PAYLOAD;
    render(Today);
    expect(screen.getByText('El día del prueba')).toBeTruthy();
    expect(screen.getByText('Y entonces escuchó una voz.')).toBeTruthy();
    expect(screen.getByText(/Día 7/)).toBeTruthy();
    expect(screen.getByTestId('state-badge').textContent).toContain('Recepción de ideas');
    expect(screen.getByTestId('panel-image').getAttribute('src')).toBe('https://x.test/1.webp');
  });

  it('shows the CTA matching RECEPCION_IDEAS', () => {
    storeState.status = 'ok';
    storeState.data = OK_PAYLOAD;
    render(Today);
    const cta = screen.getByTestId('cta');
    expect(cta.textContent).toContain('Tirá una idea');
    expect((cta as HTMLButtonElement).disabled).toBe(true);
  });

  it('shows "Votá las mejores" CTA when state=VOTACION', () => {
    storeState.status = 'ok';
    storeState.data = { ...OK_PAYLOAD, cycle_state: 'VOTACION' };
    render(Today);
    const cta = screen.getByTestId('cta');
    expect(cta.textContent).toContain('Votá las mejores');
  });

  it('renders maintenance banner with reason when status=maintenance', () => {
    storeState.status = 'maintenance';
    storeState.maintenanceReason = 'ajustando la bible';
    render(Today);
    const banner = screen.getByTestId('maintenance');
    expect(banner).toBeTruthy();
    expect(banner.textContent).toContain('ajustando la bible');
  });

  it('renders no-season banner when status=no_season', () => {
    storeState.status = 'no_season';
    render(Today);
    expect(screen.getByTestId('no-season')).toBeTruthy();
  });

  it('renders no-release banner with formatted first_release_at when status=no_release', () => {
    storeState.status = 'no_release';
    storeState.firstReleaseAt = '2026-06-11T15:00:00Z';
    render(Today);
    expect(screen.getByTestId('no-release')).toBeTruthy();
  });

  it('renders error banner with retry button when status=error', () => {
    storeState.status = 'error';
    render(Today);
    expect(screen.getByTestId('error')).toBeTruthy();
    expect(screen.getByText(/Reintentar/)).toBeTruthy();
  });
});
