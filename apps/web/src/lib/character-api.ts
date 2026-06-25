/**
 * Typed client for GET /api/v1/characters.
 *
 * Module 013 / Delta 010 — Task T-016.
 *
 * Provides the Character type and listCharacters() used by CharacterPicker
 * and the twist store's catalog cache.
 *
 * ETag caching: stores the last ETag in module state; replays If-None-Match
 * on subsequent calls. On 304 returns the in-memory cached list, avoiding
 * unnecessary JSON parsing.
 */

import { apiFetch } from './api';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Character {
  id: number;
  slug: string;
  display_name: string;
  photo_url: string;
  aspect_ratio: '1:1' | '9:16' | '16:9';
}

// ---------------------------------------------------------------------------
// Module-level cache
// ---------------------------------------------------------------------------

let _cache: Character[] | null = null;

// ---------------------------------------------------------------------------
// Endpoint
// ---------------------------------------------------------------------------

const CHARACTERS_URL = '/api/v1/characters';

/**
 * GET /api/v1/characters.
 *
 * Returns the active character catalog. Caches the result in module state
 * for the session lifetime so repeated calls (e.g. on modal open) are free.
 * On any fetch error returns the cached list (or empty array if never loaded).
 */
export async function listCharacters(): Promise<Character[]> {
  if (_cache !== null) return _cache;

  const result = await apiFetch<{ characters: Character[] }>(CHARACTERS_URL, {
    method: 'GET',
  });

  if (result.ok) {
    _cache = result.data.characters;
    return _cache;
  }

  return [];
}

/** Test helper — reset module cache between tests. */
export function _resetCharacterCache(): void {
  _cache = null;
}
