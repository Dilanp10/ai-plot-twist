/**
 * Twist store — Svelte 5 universal reactivity for /me/twists.
 *
 * Module 005 / Task T-012.
 *
 * Optimistic UI:
 *   submit() inserts a placeholder TwistMine into ``mine`` with a temp id
 *   BEFORE the network call lands, so the modal can close instantly.
 *   On 201/200 the placeholder is replaced with the server's row; on any
 *   error the placeholder is removed and the quota snapshot is restored
 *   (research R-007).
 *
 *   remove() flips an item to ``status='deleted_by_user'`` locally before
 *   the DELETE. On error the original status is restored.
 *
 * Status values:
 *   "idle"        — never loaded.
 *   "loading"     — load() in flight; ``mine`` may still hold stale data.
 *   "ok"          — data is fresh.
 *   "maintenance" — 503 under_maintenance (kill switch on).
 *   "error"       — anything else; ``errorMessage`` carries a short string.
 *
 * Uses the typed client wrappers from T-011.
 */

import {
  deleteTwist,
  freshIdempotencyKey,
  getMyTwists,
  submitTwist,
  type Quota,
  type TwistMine,
} from './twist-api';
import { listCharacters, type Character } from './character-api';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_QUOTA: Quota = { used: 0, max: 3, remaining: 3 };
const _TEMP_ID_PREFIX = 'temp-';

export type TwistStoreStatus =
  | 'idle'
  | 'loading'
  | 'ok'
  | 'maintenance'
  | 'error';

// ---------------------------------------------------------------------------
// Module-level reactive state
// ---------------------------------------------------------------------------

let _items = $state<TwistMine[]>([]);
let _quota = $state<Quota>({ ...DEFAULT_QUOTA });
let _status = $state<TwistStoreStatus>('idle');
let _errorMessage = $state<string | null>(null);

// Character catalog (Delta 010)
let _catalog = $state<Character[]>([]);
let _catalogLoaded = $state(false);
let _selectedCharacterId = $state<number | null>(null);

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
  if (code === 'over_quota') return 'Ya usaste tus 3 ideas para este capítulo.';
  if (code === 'window_closed') return 'La ventana para tirar ideas cerró.';
  if (code === 'chapter_mismatch') return 'El capítulo cambió, refrescá la página.';
  if (code === 'idempotency_conflict') return 'Reintentá: hubo un conflicto.';
  if (code === 'forbidden_not_owner') return 'Esa idea no es tuya.';
  if (code === 'twist_not_found') return 'La idea ya no existe.';
  if (code === 'already_filtered') return 'El director ya procesó esa idea.';
  if (code === 'under_maintenance') return 'El servicio está en mantenimiento.';
  if (code === 'lock_busy') return 'Reintentá en un instante.';
  if (code === 'invalid_content') return 'La idea es demasiado corta o larga.';
  if (code === 'invalid_character') return 'Ese personaje no está disponible. Elegí otro.';
  return `Error inesperado (HTTP ${status}).`;
}

function _quotaFromRemaining(remaining: number): Quota {
  return { used: _quota.max - remaining, max: _quota.max, remaining };
}

function _replaceItem(tempId: string, server: TwistMine): void {
  _items = _items.map((t) => (t.public_id === tempId ? server : t));
}

function _updateItem(
  publicId: string,
  patch: Partial<TwistMine>,
): TwistMine | null {
  let original: TwistMine | null = null;
  _items = _items.map((t) => {
    if (t.public_id === publicId) {
      original = t;
      return { ...t, ...patch };
    }
    return t;
  });
  return original;
}

// ---------------------------------------------------------------------------
// Public store
// ---------------------------------------------------------------------------

export const twistStore = {
  get mine(): TwistMine[] {
    return _items;
  },
  get quota(): Quota {
    return _quota;
  },
  get status(): TwistStoreStatus {
    return _status;
  },
  get errorMessage(): string | null {
    return _errorMessage;
  },

  // Character catalog (Delta 010)
  get catalog(): Character[] {
    return _catalog;
  },
  get catalogLoaded(): boolean {
    return _catalogLoaded;
  },
  get selectedCharacterId(): number | null {
    return _selectedCharacterId;
  },

  selectCharacter(id: number): void {
    _selectedCharacterId = id;
  },

  async ensureCatalogLoaded(): Promise<void> {
    if (_catalogLoaded) return;
    _catalog = await listCharacters();
    _catalogLoaded = true;
  },

  /**
   * Fetch /me/twists and populate the store.  Sets status='loading' first.
   */
  async load(): Promise<void> {
    _status = 'loading';
    _errorMessage = null;

    const result = await getMyTwists();
    if (result.ok) {
      _items = result.data.items;
      _quota = result.data.quota;
      _status = 'ok';
      return;
    }

    if (result.status === 503 && _problemCode(result.body) === 'under_maintenance') {
      _status = 'maintenance';
      return;
    }

    _status = 'error';
    _errorMessage = _humanError(result.status, result.body);
  },

  /**
   * Optimistic submit: insert a placeholder, fire the request, reconcile.
   *
   * On success, clears ``selectedCharacterId`` so the next submission
   * requires a fresh character pick.
   *
   * Returns ``true`` on success, ``false`` on any error (the caller can
   * decide whether to surface a toast — ``errorMessage`` is set).
   */
  async submit(chapterId: string, content: string, characterId: number): Promise<boolean> {
    const tempId = `${_TEMP_ID_PREFIX}${crypto.randomUUID()}`;
    // eslint-disable-next-line svelte/prefer-svelte-reactivity -- one-shot timestamp, not reactive
    const now = new Date().toISOString();
    const placeholder: TwistMine = {
      public_id: tempId,
      content,
      status: 'pending_review',
      submitted_at: now,
    };
    const snapshot = { items: _items, quota: _quota };

    _items = [..._items, placeholder];
    _quota = _quotaFromRemaining(Math.max(0, _quota.remaining - 1));
    _errorMessage = null;

    const idemKey = freshIdempotencyKey();
    const result = await submitTwist(chapterId, content, characterId, idemKey);

    if (result.ok) {
      _replaceItem(tempId, result.data.twist);
      _quota = _quotaFromRemaining(result.data.remaining_submissions);
      _selectedCharacterId = null;
      return true;
    }

    // Roll back the optimistic insert.
    _items = snapshot.items;
    _quota = snapshot.quota;
    _errorMessage = _humanError(result.status, result.body);
    return false;
  },

  /**
   * Optimistic delete: flip ``status='deleted_by_user'`` locally; rollback
   * on error.  Returns ``true`` on success, ``false`` otherwise.
   */
  async remove(publicId: string): Promise<boolean> {
    // eslint-disable-next-line svelte/prefer-svelte-reactivity -- one-shot timestamp, not reactive
    const optimisticDeletedAt = new Date().toISOString();
    const original = _updateItem(publicId, {
      status: 'deleted_by_user',
      deleted_at: optimisticDeletedAt,
    });
    if (original === null) {
      _errorMessage = 'No encontramos esa idea.';
      return false;
    }
    _errorMessage = null;

    const result = await deleteTwist(publicId);
    if (result.ok) {
      // Sync deleted_at with the server.
      _updateItem(publicId, { deleted_at: result.data.deleted_at });
      _quota = _quotaFromRemaining(result.data.remaining_submissions);
      return true;
    }

    // Roll back the optimistic delete.
    _updateItem(publicId, {
      status: original.status,
      deleted_at: original.deleted_at ?? null,
    });
    _errorMessage = _humanError(result.status, result.body);
    return false;
  },

  /** Test helper — reset module state between tests. */
  _reset(): void {
    _items = [];
    _quota = { ...DEFAULT_QUOTA };
    _status = 'idle';
    _errorMessage = null;
    _catalog = [];
    _catalogLoaded = false;
    _selectedCharacterId = null;
  },
};
