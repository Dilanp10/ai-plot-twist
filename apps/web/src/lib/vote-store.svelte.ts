/**
 * Vote store — Svelte 5 universal reactivity for the vote-feed.
 *
 * Module 007 / Task T-010.
 *
 * Optimistic UI:
 *   cast() flips ``has_my_vote`` + bumps ``vote_count`` on the local item
 *   BEFORE the network call lands so the tap feels instant. The quota is
 *   bumped too. On any error both deltas roll back and ``errorMessage`` is
 *   set; the special case of 409 ``already_voted`` is treated as success
 *   for UI purposes (the user already counts as having voted; the local
 *   state just needed to catch up).
 *
 * Status values:
 *   "idle"        — never loaded.
 *   "loading"     — load() in flight; ``items`` may still hold stale data.
 *   "ok"          — data is fresh.
 *   "maintenance" — 503 under_maintenance (kill switch on).
 *   "error"       — anything else; ``errorMessage`` carries a short string.
 *
 * Uses the typed client wrappers from T-009.
 */

import {
  castVote,
  getVoteFeed,
  type FeedItem,
  type PageInfo,
  type Quota,
  type SortMode,
  type VoteFeedResponse,
} from './vote-api';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_QUOTA: Quota = { used: 0, max: 5, remaining: 5 };
const DEFAULT_PAGE: PageInfo = {
  next_cursor: null,
  limit: 25,
  total_approved: 0,
};

export type VoteStoreStatus =
  | 'idle'
  | 'loading'
  | 'ok'
  | 'maintenance'
  | 'error';

// ---------------------------------------------------------------------------
// Module-level reactive state
// ---------------------------------------------------------------------------

let _items = $state<FeedItem[]>([]);
let _page = $state<PageInfo>({ ...DEFAULT_PAGE });
let _quota = $state<Quota>({ ...DEFAULT_QUOTA });
let _sort = $state<SortMode>('random');
let _status = $state<VoteStoreStatus>('idle');
let _errorMessage = $state<string | null>(null);

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

interface ProblemBody {
  code?: string;
  reason?: string | null;
  message?: string;
}

function _problemCode(body: unknown): string | null {
  if (body !== null && typeof body === 'object' && 'code' in body) {
    const code = (body as ProblemBody).code;
    return typeof code === 'string' ? code : null;
  }
  return null;
}

function _humanError(status: number, body: unknown): string {
  const code = _problemCode(body);
  if (code === 'over_quota') return 'Ya usaste todos tus votos.';
  if (code === 'window_closed') return 'La ventana de votación cerró.';
  if (code === 'twist_not_votable') return 'Esa idea ya no se puede votar.';
  if (code === 'chapter_mismatch') return 'El capítulo cambió, refrescá.';
  if (code === 'cannot_self_vote') return 'No podés votarte a vos mismo.';
  if (code === 'under_maintenance') return 'El servicio está en mantenimiento.';
  if (code === 'lock_busy') return 'Reintentá en un instante.';
  if (code === 'cursor_invalid') return 'Cursor inválido, recargá.';
  return `Error inesperado (HTTP ${status}).`;
}

function _applyPayload(payload: VoteFeedResponse, append: boolean): void {
  _items = append ? [..._items, ...payload.items] : payload.items;
  _page = payload.page;
  _quota = payload.user_quota;
}

function _quotaFromUsed(used: number): Quota {
  return { used, max: _quota.max, remaining: Math.max(0, _quota.max - used) };
}

function _patchItem(twistId: string, patch: Partial<FeedItem>): FeedItem | null {
  let original: FeedItem | null = null;
  _items = _items.map((it) => {
    if (it.id === twistId) {
      original = it;
      return { ...it, ...patch };
    }
    return it;
  });
  return original;
}

// ---------------------------------------------------------------------------
// Public store
// ---------------------------------------------------------------------------

export const voteStore = {
  get items(): FeedItem[] {
    return _items;
  },
  get page(): PageInfo {
    return _page;
  },
  get quota(): Quota {
    return _quota;
  },
  get sort(): SortMode {
    return _sort;
  },
  get status(): VoteStoreStatus {
    return _status;
  },
  get errorMessage(): string | null {
    return _errorMessage;
  },

  /**
   * Fetch the vote-feed and populate the store.
   *
   * @param opts.sort   - sort mode; defaults to current ``_sort``. Changing
   *                      it resets the items list to avoid mixing orders.
   * @param opts.cursor - if provided, the response items are APPENDED. If
   *                      omitted, the response REPLACES ``items`` (fresh load).
   * @param opts.limit  - page size; default 25.
   */
  async load(opts: { sort?: SortMode; cursor?: string | null; limit?: number } = {}): Promise<void> {
    const sort = opts.sort ?? _sort;
    const append = Boolean(opts.cursor);

    _status = 'loading';
    _errorMessage = null;
    if (sort !== _sort) {
      _sort = sort;
      _items = [];
    }

    const result = await getVoteFeed({
      sort,
      limit: opts.limit,
      cursor: opts.cursor ?? null,
    });

    if (result.ok) {
      _applyPayload(result.data, append);
      _status = 'ok';
      return;
    }

    if (
      result.status === 503 &&
      _problemCode(result.body) === 'under_maintenance'
    ) {
      _status = 'maintenance';
      return;
    }

    _status = 'error';
    _errorMessage = _humanError(result.status, result.body);
  },

  /**
   * Optimistic cast: flip ``has_my_vote`` + bump ``vote_count`` locally,
   * then fire the request and reconcile with the server response.
   *
   * Returns ``true`` on success (including the idempotent "already_voted"
   * case, since the user is in the "has voted" state either way),
   * ``false`` on any other error.
   */
  async cast(twistId: string): Promise<boolean> {
    const original = _patchItem(twistId, { has_my_vote: true });
    if (original === null) {
      _errorMessage = 'No encontramos esa idea en el feed.';
      return false;
    }
    // Apply optimistic +1 to vote_count only if the user had not yet voted.
    const didIncrement = !original.has_my_vote;
    if (didIncrement) {
      _patchItem(twistId, { vote_count: original.vote_count + 1 });
    }
    const quotaSnapshot = _quota;
    if (didIncrement) {
      _quota = _quotaFromUsed(_quota.used + 1);
    }
    _errorMessage = null;

    const result = await castVote(twistId);

    if (result.ok) {
      // Reconcile vote_count + quota with the server's authoritative values.
      _patchItem(twistId, {
        vote_count: result.data.new_vote_count,
        has_my_vote: true,
      });
      _quota = result.data.user_quota;
      return true;
    }

    // 409 already_voted: server says we ALREADY voted. Keep the optimistic
    // flag but drop the +1 we added — the server already counted it before.
    if (_problemCode(result.body) === 'already_voted') {
      if (didIncrement) {
        _patchItem(twistId, { vote_count: original.vote_count });
        _quota = quotaSnapshot;
      }
      return true;
    }

    // Roll back the optimistic delta.
    _patchItem(twistId, {
      has_my_vote: original.has_my_vote,
      vote_count: original.vote_count,
    });
    _quota = quotaSnapshot;
    _errorMessage = _humanError(result.status, result.body);
    return false;
  },

  /** Test helper — reset module state between tests. */
  _reset(): void {
    _items = [];
    _page = { ...DEFAULT_PAGE };
    _quota = { ...DEFAULT_QUOTA };
    _sort = 'random';
    _status = 'idle';
    _errorMessage = null;
  },
};
