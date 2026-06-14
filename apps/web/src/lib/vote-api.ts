/**
 * Typed client wrappers for the module 007 voting endpoints.
 *
 * Module 007 / Task T-009.
 *
 * Reuses ``apiFetch`` from module 002 so the JWT interceptor + refresh
 * logic is inherited transparently.
 */

import { apiFetch, type ApiResult } from './api';

// ---------------------------------------------------------------------------
// Response types — mirror specs/007-voting/contracts/voting.yaml
// ---------------------------------------------------------------------------

export type SortMode = 'random' | 'recent' | 'hot';

export interface FeedItem {
  id: string; // UUID — the twist's public_id
  content: string;
  vote_count: number;
  has_my_vote: boolean;
}

export interface PageInfo {
  next_cursor: string | null;
  limit: number;
  total_approved: number;
}

export interface Quota {
  used: number;
  max: number;
  remaining: number;
}

export interface VoteFeedResponse {
  items: FeedItem[];
  page: PageInfo;
  user_quota: Quota;
}

export interface VoteResponse {
  twist_id: string;
  new_vote_count: number;
  user_quota: Quota;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

const FEED_URL = '/api/v1/twists/vote-feed';
const CAST_URL = '/api/v1/twists/vote';

/**
 * GET /api/v1/twists/vote-feed.
 *
 * @param opts.sort   - one of 'random' | 'recent' | 'hot'. Default 'random'.
 * @param opts.limit  - 1..100. Default 25.
 * @param opts.cursor - opaque token returned by a prior call's
 *                      `page.next_cursor`. Pass to advance.
 *
 * Responses:
 *   - 200 → VoteFeedResponse
 *   - 409 → window_closed
 *   - 422 → cursor_invalid
 *   - 503 → under_maintenance
 */
export async function getVoteFeed(opts: {
  sort?: SortMode;
  limit?: number;
  cursor?: string | null;
} = {}): Promise<ApiResult<VoteFeedResponse>> {
  const params = new URLSearchParams();
  if (opts.sort) params.set('sort', opts.sort);
  if (opts.limit !== undefined) params.set('limit', String(opts.limit));
  if (opts.cursor) params.set('cursor', opts.cursor);
  const qs = params.toString();
  const url = qs ? `${FEED_URL}?${qs}` : FEED_URL;
  return apiFetch<VoteFeedResponse>(url, { method: 'GET' });
}

/**
 * POST /api/v1/twists/vote.
 *
 * Naturally idempotent on (twist_id, user_id) — re-firing a previously
 * successful vote returns 409 ``already_voted``. The optimistic UI in
 * vote-store treats a 409 as the "already counted" state.
 *
 * Responses:
 *   - 200 → VoteResponse
 *   - 409 → window_closed | over_quota | already_voted
 *           | twist_not_votable | chapter_mismatch | cannot_self_vote
 *   - 503 → under_maintenance | lock_busy
 */
export async function castVote(
  twistId: string,
): Promise<ApiResult<VoteResponse>> {
  return apiFetch<VoteResponse>(CAST_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ twist_id: twistId }),
  });
}
