/**
 * Unit tests for vote-store (T-010).
 *
 * Mocks vote-api so we exercise the store's load + cast flows + rollback
 * without hitting fetch.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type {
  FeedItem,
  PageInfo,
  Quota,
  VoteFeedResponse,
  VoteResponse,
} from '../src/lib/vote-api';
import { voteStore } from '../src/lib/vote-store.svelte';

const { mockGetFeed, mockCast } = vi.hoisted(() => ({
  mockGetFeed: vi.fn(),
  mockCast: vi.fn(),
}));

vi.mock('../src/lib/vote-api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/lib/vote-api')>();
  return {
    ...actual,
    getVoteFeed: mockGetFeed,
    castVote: mockCast,
  };
});

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const TWIST_A = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';
const TWIST_B = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb';

function feedItem(overrides: Partial<FeedItem> = {}): FeedItem {
  return {
    id: TWIST_A,
    content: 'algo',
    vote_count: 0,
    has_my_vote: false,
    ...overrides,
  };
}

function feedPayload(overrides: Partial<VoteFeedResponse> = {}): VoteFeedResponse {
  const items: FeedItem[] = [feedItem({ id: TWIST_A }), feedItem({ id: TWIST_B })];
  const page: PageInfo = { next_cursor: null, limit: 25, total_approved: 2 };
  const user_quota: Quota = { used: 0, max: 5, remaining: 5 };
  return { items, page, user_quota, ...overrides };
}

function castOk(twistId: string, newVoteCount: number, used: number): { ok: true; data: VoteResponse } {
  return {
    ok: true,
    data: {
      twist_id: twistId,
      new_vote_count: newVoteCount,
      user_quota: { used, max: 5, remaining: 5 - used },
    },
  };
}

beforeEach(() => {
  voteStore._reset();
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// load
// ---------------------------------------------------------------------------

describe('voteStore.load', () => {
  it('populates items + page + quota on 200', async () => {
    mockGetFeed.mockResolvedValue({ ok: true, data: feedPayload() });
    await voteStore.load();

    expect(voteStore.status).toBe('ok');
    expect(voteStore.items).toHaveLength(2);
    expect(voteStore.page.total_approved).toBe(2);
    expect(voteStore.quota).toEqual({ used: 0, max: 5, remaining: 5 });
  });

  it('sets status=maintenance on 503 under_maintenance', async () => {
    mockGetFeed.mockResolvedValue({
      ok: false,
      status: 503,
      body: { code: 'under_maintenance' },
    });
    await voteStore.load();
    expect(voteStore.status).toBe('maintenance');
  });

  it('sets status=error + humanized message on 409 window_closed', async () => {
    mockGetFeed.mockResolvedValue({
      ok: false,
      status: 409,
      body: { code: 'window_closed' },
    });
    await voteStore.load();
    expect(voteStore.status).toBe('error');
    expect(voteStore.errorMessage).toMatch(/ventana/i);
  });

  it('appends items when a cursor is provided', async () => {
    mockGetFeed.mockResolvedValueOnce({ ok: true, data: feedPayload() });
    await voteStore.load();
    expect(voteStore.items).toHaveLength(2);

    const page2: VoteFeedResponse = {
      items: [feedItem({ id: 'cccccccc-cccc-cccc-cccc-cccccccccccc' })],
      page: { next_cursor: null, limit: 25, total_approved: 3 },
      user_quota: { used: 0, max: 5, remaining: 5 },
    };
    mockGetFeed.mockResolvedValueOnce({ ok: true, data: page2 });
    await voteStore.load({ cursor: 'some-cursor' });
    expect(voteStore.items).toHaveLength(3);
  });

  it('switching sort clears items before fetching', async () => {
    mockGetFeed.mockResolvedValueOnce({ ok: true, data: feedPayload() });
    await voteStore.load(); // default 'random'
    expect(voteStore.items).toHaveLength(2);

    const recent: VoteFeedResponse = {
      items: [feedItem({ id: 'dddddddd-dddd-dddd-dddd-dddddddddddd' })],
      page: { next_cursor: null, limit: 25, total_approved: 1 },
      user_quota: { used: 0, max: 5, remaining: 5 },
    };
    mockGetFeed.mockResolvedValueOnce({ ok: true, data: recent });
    await voteStore.load({ sort: 'recent' });
    expect(voteStore.sort).toBe('recent');
    expect(voteStore.items).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// cast
// ---------------------------------------------------------------------------

describe('voteStore.cast', () => {
  beforeEach(async () => {
    mockGetFeed.mockResolvedValue({ ok: true, data: feedPayload() });
    await voteStore.load();
  });

  it('optimistically flips has_my_vote and bumps vote_count', async () => {
    // Hold the network promise so we can inspect the optimistic state.
    let resolveCast: (value: { ok: true; data: VoteResponse }) => void = () => {};
    mockCast.mockReturnValue(
      new Promise((res) => {
        resolveCast = res;
      }),
    );
    const pending = voteStore.cast(TWIST_A);
    expect(voteStore.items.find((it) => it.id === TWIST_A)?.has_my_vote).toBe(
      true,
    );
    expect(voteStore.items.find((it) => it.id === TWIST_A)?.vote_count).toBe(1);
    expect(voteStore.quota.used).toBe(1);

    resolveCast(castOk(TWIST_A, 1, 1));
    await pending;

    expect(voteStore.items.find((it) => it.id === TWIST_A)?.vote_count).toBe(1);
    expect(voteStore.quota.used).toBe(1);
  });

  it('reconciles vote_count + quota with server values on success', async () => {
    // Simulate another user having voted between our load and our cast.
    mockCast.mockResolvedValue(castOk(TWIST_A, 7, 1));
    const ok = await voteStore.cast(TWIST_A);
    expect(ok).toBe(true);
    expect(voteStore.items.find((it) => it.id === TWIST_A)?.vote_count).toBe(7);
  });

  it('rolls back on 409 over_quota', async () => {
    mockCast.mockResolvedValue({
      ok: false,
      status: 409,
      body: { code: 'over_quota', quota_used: 5, quota_max: 5 },
    });
    const ok = await voteStore.cast(TWIST_A);
    expect(ok).toBe(false);
    expect(voteStore.items.find((it) => it.id === TWIST_A)?.has_my_vote).toBe(
      false,
    );
    expect(voteStore.items.find((it) => it.id === TWIST_A)?.vote_count).toBe(0);
    expect(voteStore.quota.used).toBe(0);
    expect(voteStore.errorMessage).toMatch(/votos/i);
  });

  it('treats 409 already_voted as success (idempotent path)', async () => {
    mockCast.mockResolvedValue({
      ok: false,
      status: 409,
      body: { code: 'already_voted', twist_id: TWIST_A },
    });
    const ok = await voteStore.cast(TWIST_A);
    expect(ok).toBe(true);
    expect(voteStore.items.find((it) => it.id === TWIST_A)?.has_my_vote).toBe(
      true,
    );
    // The +1 we optimistically added is rolled back because the server
    // counted us before; the original vote_count stands.
    expect(voteStore.items.find((it) => it.id === TWIST_A)?.vote_count).toBe(0);
    expect(voteStore.errorMessage).toBeNull();
  });

  it('does NOT double-count when the item already has has_my_vote=true', async () => {
    // Seed with an item the user already voted for.
    mockGetFeed.mockResolvedValue({
      ok: true,
      data: {
        items: [feedItem({ id: TWIST_A, vote_count: 3, has_my_vote: true })],
        page: { next_cursor: null, limit: 25, total_approved: 1 },
        user_quota: { used: 1, max: 5, remaining: 4 },
      },
    });
    await voteStore.load();
    expect(voteStore.quota.used).toBe(1);

    mockCast.mockResolvedValue(castOk(TWIST_A, 3, 1));
    await voteStore.cast(TWIST_A);
    expect(voteStore.quota.used).toBe(1); // not 2
  });

  it('returns false when the twist is not in the local feed', async () => {
    const ok = await voteStore.cast('ffffffff-ffff-ffff-ffff-ffffffffffff');
    expect(ok).toBe(false);
    expect(voteStore.errorMessage).toMatch(/no encontramos/i);
  });
});
