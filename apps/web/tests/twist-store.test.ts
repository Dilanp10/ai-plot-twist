/**
 * Unit tests for twist-store (T-012).
 *
 * Mocks twist-api so we exercise the store's optimistic flow + rollback
 * without hitting fetch.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { twistStore } from '../src/lib/twist-store.svelte';
import type {
  DeleteResponse,
  MeTwistsResponse,
  SubmitResponse,
  TwistMine,
} from '../src/lib/twist-api';

// ---------------------------------------------------------------------------
// twist-api mock (hoisted)
// ---------------------------------------------------------------------------

const { mockSubmit, mockDelete, mockGetMine } = vi.hoisted(() => ({
  mockSubmit: vi.fn(),
  mockDelete: vi.fn(),
  mockGetMine: vi.fn(),
}));

vi.mock('../src/lib/twist-api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/lib/twist-api')>();
  return {
    ...actual,
    submitTwist: mockSubmit,
    deleteTwist: mockDelete,
    getMyTwists: mockGetMine,
    // freshIdempotencyKey stays real (deterministic-enough via crypto).
  };
});

// ---------------------------------------------------------------------------
// Fixtures + helpers
// ---------------------------------------------------------------------------

const CHAPTER_ID = '11111111-1111-1111-1111-111111111111';
const TWIST_ID = '22222222-2222-2222-2222-222222222222';

function twistFixture(overrides: Partial<TwistMine> = {}): TwistMine {
  return {
    public_id: TWIST_ID,
    content: 'idea cualquiera',
    status: 'pending_review',
    submitted_at: '2026-06-12T18:00:00Z',
    ...overrides,
  };
}

beforeEach(() => {
  twistStore._reset();
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// load
// ---------------------------------------------------------------------------

describe('twistStore.load', () => {
  it('populates mine + quota on 200', async () => {
    const payload: MeTwistsResponse = {
      items: [twistFixture()],
      quota: { used: 1, max: 3, remaining: 2 },
    };
    mockGetMine.mockResolvedValue({ ok: true, data: payload });

    await twistStore.load();

    expect(twistStore.status).toBe('ok');
    expect(twistStore.mine).toHaveLength(1);
    expect(twistStore.mine[0].public_id).toBe(TWIST_ID);
    expect(twistStore.quota).toEqual({ used: 1, max: 3, remaining: 2 });
  });

  it('sets status=maintenance on 503 under_maintenance', async () => {
    mockGetMine.mockResolvedValue({
      ok: false,
      status: 503,
      body: { code: 'under_maintenance' },
    });
    await twistStore.load();
    expect(twistStore.status).toBe('maintenance');
  });

  it('sets status=error with humanized message on other failures', async () => {
    mockGetMine.mockResolvedValue({
      ok: false,
      status: 500,
      body: null,
    });
    await twistStore.load();
    expect(twistStore.status).toBe('error');
    expect(twistStore.errorMessage).toMatch(/inesperado/i);
  });
});

// ---------------------------------------------------------------------------
// submit (optimistic)
// ---------------------------------------------------------------------------

describe('twistStore.submit', () => {
  it('inserts a placeholder, then replaces with the server twist on success', async () => {
    const server: SubmitResponse = {
      twist: twistFixture(),
      remaining_submissions: 2,
    };
    mockSubmit.mockResolvedValue({ ok: true, data: server });

    const ok = await twistStore.submit(CHAPTER_ID, 'Una idea fresca');
    expect(ok).toBe(true);

    // Final state: 1 item with the server-side public_id.
    expect(twistStore.mine).toHaveLength(1);
    expect(twistStore.mine[0].public_id).toBe(TWIST_ID);
    expect(twistStore.mine[0].public_id.startsWith('temp-')).toBe(false);
    expect(twistStore.quota).toEqual({ used: 1, max: 3, remaining: 2 });
  });

  it('rolls back the optimistic insert + quota on error', async () => {
    mockSubmit.mockResolvedValue({
      ok: false,
      status: 409,
      body: { code: 'over_quota', quota_used: 3, quota_max: 3 },
    });

    const ok = await twistStore.submit(CHAPTER_ID, 'Otra idea');
    expect(ok).toBe(false);

    expect(twistStore.mine).toHaveLength(0);
    expect(twistStore.quota).toEqual({ used: 0, max: 3, remaining: 3 });
    expect(twistStore.errorMessage).toMatch(/3 ideas/);
  });
});

// ---------------------------------------------------------------------------
// remove (optimistic)
// ---------------------------------------------------------------------------

describe('twistStore.remove', () => {
  it('flips status to deleted_by_user locally and syncs deleted_at on success', async () => {
    // Seed an existing item.
    mockGetMine.mockResolvedValue({
      ok: true,
      data: {
        items: [twistFixture()],
        quota: { used: 1, max: 3, remaining: 2 },
      } satisfies MeTwistsResponse,
    });
    await twistStore.load();

    const server: DeleteResponse = {
      twist_id: TWIST_ID,
      deleted_at: '2026-06-12T18:30:00Z',
      remaining_submissions: 2,
    };
    mockDelete.mockResolvedValue({ ok: true, data: server });

    const ok = await twistStore.remove(TWIST_ID);
    expect(ok).toBe(true);

    expect(twistStore.mine[0].status).toBe('deleted_by_user');
    expect(twistStore.mine[0].deleted_at).toBe('2026-06-12T18:30:00Z');
    expect(twistStore.quota.remaining).toBe(2); // not freed (FR-004)
  });

  it('rolls back to original status on error', async () => {
    mockGetMine.mockResolvedValue({
      ok: true,
      data: {
        items: [twistFixture()],
        quota: { used: 1, max: 3, remaining: 2 },
      } satisfies MeTwistsResponse,
    });
    await twistStore.load();

    mockDelete.mockResolvedValue({
      ok: false,
      status: 409,
      body: { code: 'already_filtered' },
    });

    const ok = await twistStore.remove(TWIST_ID);
    expect(ok).toBe(false);

    expect(twistStore.mine[0].status).toBe('pending_review');
    expect(twistStore.mine[0].deleted_at).toBeFalsy();
    expect(twistStore.errorMessage).toMatch(/director/i);
  });

  it('returns false for unknown public_id with a friendly error', async () => {
    const ok = await twistStore.remove('unknown-id');
    expect(ok).toBe(false);
    expect(twistStore.errorMessage).toMatch(/no encontramos/i);
  });
});
