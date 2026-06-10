/**
 * Chapter store — Svelte 5 universal reactivity for /chapters/today.
 *
 * Module 004 / Task T-012.
 *
 * Two-call API:
 *   chapterStore.load()    — explicit fetch; sets status="loading" first so
 *                            the route can show a skeleton. Use on mount.
 *   chapterStore.refresh() — SWR-style background fetch; does NOT clear
 *                            ``data`` while in flight, so the route keeps
 *                            showing the previous chapter until the new
 *                            payload lands. Use on focus / interval.
 *
 * Status values mirror the HTTP outcomes:
 *   "idle"         — never loaded yet.
 *   "loading"      — load() in flight, data is stale or null.
 *   "ok"           — data is the freshest TodayResponse.
 *   "maintenance"  — 503 under_maintenance from kill-switch.
 *                    ``maintenanceReason`` carries the operator's note.
 *   "no_season"    — 503 no_active_season (PO has not bootstrapped yet).
 *   "no_release"   — 404 no_live_chapter (cycle exists but day 1 has not
 *                    been released). ``firstReleaseAt`` carries the ISO ts.
 *   "error"        — anything else; ``errorBody`` is the raw response.
 *
 * Uses ``apiFetch`` from module 002 so the JWT interceptor + refresh logic
 * are inherited transparently. ``/chapters/today`` does not require auth,
 * but apiFetch attaches the JWT when present — the backend ignores invalid
 * auth headers on public reads (spec edge case).
 */

import { apiFetch } from './api';

// ---------------------------------------------------------------------------
// API response shape — matches contracts/chapters.yaml#TodayResponse
// ---------------------------------------------------------------------------

export type CycleState =
  | 'PENDING_RELEASE'
  | 'ESTRENO'
  | 'RECEPCION_IDEAS'
  | 'FILTERING'
  | 'VOTACION'
  | 'GENERACION'
  | 'FAILED';

export interface SeasonBrief {
  slug: string;
  title: string;
}

export interface Panel {
  idx: number;
  image_url: string;
  image_blurhash?: string | null;
  tts_url?: string | null;
  narration: string;
  mood: string;
}

export interface ChapterPayload {
  id: string;
  day_index: number;
  title: string;
  synopsis: string;
  released_at: string; // ISO 8601 UTC
  panels: Panel[];
  cliffhanger: string;
}

export interface Windows {
  submit_until: string;
  vote_from: string;
  vote_until: string;
  next_release: string;
}

export interface TodayResponse {
  cycle_state: CycleState;
  season: SeasonBrief;
  chapter: ChapterPayload;
  windows: Windows;
}

export type ChapterStatus =
  | 'idle'
  | 'loading'
  | 'ok'
  | 'maintenance'
  | 'no_season'
  | 'no_release'
  | 'error';

// ---------------------------------------------------------------------------
// Module-level reactive state
// ---------------------------------------------------------------------------

let _data = $state<TodayResponse | null>(null);
let _status = $state<ChapterStatus>('idle');
let _maintenanceReason = $state<string | null>(null);
let _firstReleaseAt = $state<string | null>(null);
let _errorBody = $state<unknown>(null);

// ---------------------------------------------------------------------------
// Internals — body shape helpers (no throwing; we trust the backend contract)
// ---------------------------------------------------------------------------

const TODAY_URL = '/api/v1/chapters/today';

interface ProblemBody {
  code?: string;
  reason?: string | null;
  first_release_at?: string;
}

function _problemCode(body: unknown): string | null {
  if (body !== null && typeof body === 'object' && 'code' in body) {
    const code = (body as ProblemBody).code;
    return typeof code === 'string' ? code : null;
  }
  return null;
}

function _problemReason(body: unknown): string | null {
  if (body !== null && typeof body === 'object' && 'reason' in body) {
    const r = (body as ProblemBody).reason;
    return typeof r === 'string' ? r : null;
  }
  return null;
}

function _firstReleaseFrom(body: unknown): string | null {
  if (body !== null && typeof body === 'object' && 'first_release_at' in body) {
    const v = (body as ProblemBody).first_release_at;
    return typeof v === 'string' ? v : null;
  }
  return null;
}

async function _fetchOnce(keepStaleData: boolean): Promise<void> {
  if (!keepStaleData) {
    _data = null;
  }
  _maintenanceReason = null;
  _firstReleaseAt = null;
  _errorBody = null;

  const result = await apiFetch<TodayResponse>(TODAY_URL);

  if (result.ok) {
    _data = result.data;
    _status = 'ok';
    return;
  }

  const code = _problemCode(result.body);

  if (result.status === 503 && code === 'under_maintenance') {
    _status = 'maintenance';
    _maintenanceReason = _problemReason(result.body);
    return;
  }
  if (result.status === 503 && code === 'no_active_season') {
    _status = 'no_season';
    return;
  }
  if (result.status === 404 && code === 'no_live_chapter') {
    _status = 'no_release';
    _firstReleaseAt = _firstReleaseFrom(result.body);
    return;
  }

  _status = 'error';
  _errorBody = result.body;
}

// ---------------------------------------------------------------------------
// Public store
// ---------------------------------------------------------------------------

export const chapterStore = {
  get data(): TodayResponse | null {
    return _data;
  },
  get status(): ChapterStatus {
    return _status;
  },
  get maintenanceReason(): string | null {
    return _maintenanceReason;
  },
  get firstReleaseAt(): string | null {
    return _firstReleaseAt;
  },
  get errorBody(): unknown {
    return _errorBody;
  },

  /**
   * Explicit fetch. Sets ``status="loading"`` immediately, then resolves to
   * one of the terminal statuses. Use on mount.
   */
  async load(): Promise<void> {
    _status = 'loading';
    await _fetchOnce(false);
  },

  /**
   * SWR-style background fetch. Does NOT change ``status`` to "loading" and
   * does NOT clear ``data`` while in flight — the route keeps rendering the
   * previous payload until the new one is ready. Use on focus or interval.
   */
  async refresh(): Promise<void> {
    await _fetchOnce(true);
  },

  /** Test helper — reset module state between tests. */
  _reset(): void {
    _data = null;
    _status = 'idle';
    _maintenanceReason = null;
    _firstReleaseAt = null;
    _errorBody = null;
  },
};
