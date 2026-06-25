/**
 * Typed client wrappers for the module 005 twist endpoints.
 *
 * Module 005 / Task T-011.
 *
 * Reuses ``apiFetch`` from module 002 so the JWT interceptor + refresh
 * logic is inherited transparently.
 *
 * Idempotency-Key handling:
 *   - submitTwist() generates a fresh UUID per CALL (not per retry).
 *     Retries of the SAME failed call should reuse the returned key.
 *   - The caller is responsible for retry orchestration; this module
 *     only exposes the primitive.
 */

import { apiFetch, type ApiResult } from './api';

// ---------------------------------------------------------------------------
// Response types — mirror contracts/twists.yaml
// ---------------------------------------------------------------------------

export type TwistStatus =
  | 'pending_review'
  | 'approved'
  | 'rejected_offensive'
  | 'rejected_incoherent'
  | 'rejected_spam'
  | 'deleted_by_user';

export interface CharacterInTwist {
  id: number;
  slug: string;
  display_name: string;
  photo_url: string;
}

export interface TwistMine {
  public_id: string;
  content: string;
  status: TwistStatus;
  director_reason?: string | null;
  submitted_at: string; // ISO 8601 UTC
  deleted_at?: string | null;
  character?: CharacterInTwist | null;
}

export interface Quota {
  used: number;
  max: number;
  remaining: number;
}

export interface SubmitResponse {
  twist: TwistMine;
  remaining_submissions: number;
}

export interface DeleteResponse {
  twist_id: string;
  deleted_at: string;
  remaining_submissions: number;
}

export interface MeTwistsResponse {
  items: TwistMine[];
  quota: Quota;
}

// ---------------------------------------------------------------------------
// UUID generator (Web Crypto, available in all evergreen browsers + jsdom)
// ---------------------------------------------------------------------------

/**
 * Generate a fresh Idempotency-Key.  Exported for the caller to reuse
 * the same key across retries of the SAME failed submit attempt.
 */
export function freshIdempotencyKey(): string {
  return crypto.randomUUID();
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

const SUBMIT_URL = '/api/v1/twists/submit';
const ME_TWISTS_URL = '/api/v1/me/twists';

/**
 * POST /api/v1/twists/submit.
 *
 * @param chapterId  - The current live chapter's public_id (UUID).
 * @param content    - Raw text; server normalizes (NFKC + control-strip + trim).
 * @param idempotencyKey - Override the auto-generated key.  Pass the SAME
 *                         key on retries of a failed attempt; pass a NEW
 *                         key for distinct submissions.
 *
 * Responses:
 *   - 201 → SubmitResponse  (fresh insert)
 *   - 200 → SubmitResponse  (idempotent replay of a prior call)
 *   - 409 → { code: window_closed | over_quota | chapter_mismatch
 *                 | idempotency_conflict, ... }
 *   - 422 → invalid_content / missing_idempotency_key
 *   - 503 → under_maintenance / lock_busy
 */
export async function submitTwist(
  chapterId: string,
  content: string,
  characterId: number,
  idempotencyKey: string = freshIdempotencyKey(),
): Promise<ApiResult<SubmitResponse>> {
  return apiFetch<SubmitResponse>(SUBMIT_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
    },
    body: JSON.stringify({ chapter_id: chapterId, content, character_id: characterId }),
  });
}

/**
 * DELETE /api/v1/twists/{public_id}.
 *
 * Idempotent at the server level: re-DELETE of an already-deleted twist
 * returns 200 with the original ``deleted_at``.
 */
export async function deleteTwist(
  publicId: string,
): Promise<ApiResult<DeleteResponse>> {
  return apiFetch<DeleteResponse>(`/api/v1/twists/${publicId}`, {
    method: 'DELETE',
  });
}

/**
 * GET /api/v1/me/twists.  Always returns 200 (possibly empty) for an
 * authenticated user, even when no live chapter exists.
 */
export async function getMyTwists(): Promise<ApiResult<MeTwistsResponse>> {
  return apiFetch<MeTwistsResponse>(ME_TWISTS_URL, { method: 'GET' });
}
