/**
 * Component tests for the /vote route (T-012).
 *
 * Mocks voteStore to keep the tests offline. Asserts that:
 *   - loading skeleton renders when no items yet,
 *   - items render through VoteCard once loaded,
 *   - sort tabs switch correctly,
 *   - "Cargar más" appears when next_cursor is non-null,
 *   - quota-exhausted banner shows when remaining=0,
 *   - empty state shows when items=[].
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import Vote from '../src/routes/vote.svelte';

const { mockStore, mockLoad, mockCast } = vi.hoisted(() => {
  const mockLoad = vi.fn();
  const mockCast = vi.fn();
  const mockStore = {
    items: [] as Array<{ id: string; content: string; vote_count: number; has_my_vote: boolean }>,
    page: { next_cursor: null as string | null, limit: 25, total_approved: 0 },
    quota: { used: 0, max: 5, remaining: 5 },
    sort: 'random' as 'random' | 'recent' | 'hot',
    status: 'ok' as 'idle' | 'loading' | 'ok' | 'maintenance' | 'error',
    errorMessage: null as string | null,
    load: mockLoad,
    cast: mockCast,
  };
  return { mockStore, mockLoad, mockCast };
});

vi.mock('../src/lib/vote-store.svelte', () => ({ voteStore: mockStore }));
vi.mock('../src/lib/router.svelte', () => ({
  router: { navigate: vi.fn() },
}));

function resetStore() {
  mockStore.items = [];
  mockStore.page = { next_cursor: null, limit: 25, total_approved: 0 };
  mockStore.quota = { used: 0, max: 5, remaining: 5 };
  mockStore.sort = 'random';
  mockStore.status = 'ok';
  mockStore.errorMessage = null;
}

beforeEach(() => {
  resetStore();
  mockLoad.mockReset();
  mockCast.mockReset();
});

afterEach(() => {
  cleanup();
});

describe('Vote route', () => {
  it('renders the loading skeleton when no items have arrived', () => {
    mockStore.status = 'loading';
    render(Vote);
    expect(screen.getByTestId('loading')).toBeTruthy();
  });

  it('calls voteStore.load on mount', () => {
    render(Vote);
    expect(mockLoad).toHaveBeenCalledTimes(1);
  });

  it('renders one VoteCard per item', () => {
    mockStore.items = [
      { id: '11111111-1111-1111-1111-111111111111', content: 'Idea A', vote_count: 2, has_my_vote: false },
      { id: '22222222-2222-2222-2222-222222222222', content: 'Idea B', vote_count: 0, has_my_vote: true },
    ];
    render(Vote);
    expect(screen.getByText('Idea A')).toBeTruthy();
    expect(screen.getByText('Idea B')).toBeTruthy();
  });

  it('shows the empty state when items=[] and status=ok', () => {
    render(Vote);
    expect(screen.getByTestId('empty')).toBeTruthy();
  });

  it('shows the maintenance banner when status=maintenance', () => {
    mockStore.status = 'maintenance';
    render(Vote);
    expect(screen.getByTestId('maintenance')).toBeTruthy();
  });

  it('shows the error banner when status=error and errorMessage is set', () => {
    mockStore.status = 'error';
    mockStore.errorMessage = 'algo se rompió';
    render(Vote);
    expect(screen.getByTestId('error')).toBeTruthy();
    expect(screen.getByText('algo se rompió')).toBeTruthy();
  });

  it('switches sort when a sort tab is clicked', async () => {
    mockStore.items = [
      { id: '11111111-1111-1111-1111-111111111111', content: 'X', vote_count: 0, has_my_vote: false },
    ];
    render(Vote);
    mockLoad.mockClear();
    await fireEvent.click(screen.getByText('Populares'));
    expect(mockLoad).toHaveBeenCalledWith({ sort: 'hot' });
  });

  it('does NOT trigger load when clicking the already-active sort', async () => {
    mockStore.items = [
      { id: '11111111-1111-1111-1111-111111111111', content: 'X', vote_count: 0, has_my_vote: false },
    ];
    render(Vote);
    mockLoad.mockClear();
    await fireEvent.click(screen.getByText('Para vos'));
    expect(mockLoad).not.toHaveBeenCalled();
  });

  it('renders "Cargar más" when next_cursor is non-null', () => {
    mockStore.items = [
      { id: '11111111-1111-1111-1111-111111111111', content: 'A', vote_count: 0, has_my_vote: false },
    ];
    mockStore.page.next_cursor = 'cursor-abc';
    render(Vote);
    expect(screen.getByRole('button', { name: /cargar más/i })).toBeTruthy();
  });

  it('hides "Cargar más" when next_cursor is null', () => {
    mockStore.items = [
      { id: '11111111-1111-1111-1111-111111111111', content: 'A', vote_count: 0, has_my_vote: false },
    ];
    render(Vote);
    expect(screen.queryByRole('button', { name: /cargar más/i })).toBeNull();
  });

  it('shows the quota-exhausted banner when remaining=0', () => {
    mockStore.items = [
      { id: '11111111-1111-1111-1111-111111111111', content: 'A', vote_count: 0, has_my_vote: false },
    ];
    mockStore.quota = { used: 5, max: 5, remaining: 0 };
    render(Vote);
    expect(screen.getByTestId('quota-exhausted')).toBeTruthy();
  });

  it('shows a toast when errorMessage is set but status is not error', () => {
    mockStore.items = [
      { id: '11111111-1111-1111-1111-111111111111', content: 'A', vote_count: 0, has_my_vote: false },
    ];
    mockStore.errorMessage = 'la red falló';
    render(Vote);
    expect(screen.getByTestId('toast')).toBeTruthy();
  });
});
