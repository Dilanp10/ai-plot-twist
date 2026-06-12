/**
 * Component tests for MyTwistsPanel (T-013).
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import MyTwistsPanel from '../src/lib/components/MyTwistsPanel.svelte';
import type { TwistMine } from '../src/lib/twist-api';

// ---------------------------------------------------------------------------
// twistStore mock (hoisted)
// ---------------------------------------------------------------------------

const { mockLoad, mockRemove, mockStore } = vi.hoisted(() => {
  const mockLoad = vi.fn().mockResolvedValue(undefined);
  const mockRemove = vi.fn().mockResolvedValue(true);
  const mockStore = {
    mine: [] as TwistMine[],
    quota: { used: 0, max: 3, remaining: 3 },
    status: 'ok' as
      | 'idle'
      | 'loading'
      | 'ok'
      | 'maintenance'
      | 'error',
    errorMessage: null as string | null,
    load: mockLoad,
    remove: mockRemove,
  };
  return { mockLoad, mockRemove, mockStore };
});

vi.mock('../src/lib/twist-store.svelte', () => ({ twistStore: mockStore }));

function twist(overrides: Partial<TwistMine> = {}): TwistMine {
  return {
    public_id: '22222222-2222-2222-2222-222222222222',
    content: 'idea cualquiera',
    status: 'pending_review',
    submitted_at: '2026-06-12T18:00:00Z',
    ...overrides,
  };
}

beforeEach(() => {
  mockLoad.mockClear();
  mockRemove.mockClear();
  mockStore.mine = [];
  mockStore.quota = { used: 0, max: 3, remaining: 3 };
  mockStore.status = 'ok';
  mockStore.errorMessage = null;
});

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------

describe('MyTwistsPanel', () => {
  it('calls twistStore.load on mount', () => {
    render(MyTwistsPanel, { props: { canDelete: true } });
    expect(mockLoad).toHaveBeenCalledTimes(1);
  });

  it('shows the empty state message when there are no twists', async () => {
    render(MyTwistsPanel, { props: { canDelete: true } });
    await waitFor(() => {
      expect(screen.getByText(/Todavía no tiraste/i)).toBeTruthy();
    });
  });

  it('renders items with status badges and quota footer', async () => {
    mockStore.mine = [
      twist({ public_id: 'a', content: 'idea uno xxxxx', status: 'pending_review' }),
      twist({ public_id: 'b', content: 'idea dos xxxxx', status: 'approved' }),
    ];
    mockStore.quota = { used: 2, max: 3, remaining: 1 };

    render(MyTwistsPanel, { props: { canDelete: true } });

    await waitFor(() => {
      expect(screen.getByText('idea uno xxxxx')).toBeTruthy();
    });
    expect(screen.getByText('idea dos xxxxx')).toBeTruthy();
    expect(screen.getByText('Pendiente')).toBeTruthy();
    expect(screen.getByText('Aprobada')).toBeTruthy();
    expect(screen.getByText(/Te quedan 1 ideas/)).toBeTruthy();
  });

  it('shows the delete button only for pending_review when canDelete=true', async () => {
    mockStore.mine = [
      twist({ public_id: 'p', content: 'idea pending', status: 'pending_review' }),
      twist({ public_id: 'a', content: 'idea approved', status: 'approved' }),
    ];

    render(MyTwistsPanel, { props: { canDelete: true } });
    await waitFor(() => {
      expect(screen.getByText('idea pending')).toBeTruthy();
    });

    const deleteBtns = screen.getAllByRole('button', { name: /Borrar/i });
    expect(deleteBtns).toHaveLength(1);
  });

  it('hides delete buttons when canDelete=false (window closed)', async () => {
    mockStore.mine = [
      twist({ public_id: 'p', content: 'idea pending', status: 'pending_review' }),
    ];

    render(MyTwistsPanel, { props: { canDelete: false } });
    await waitFor(() => {
      expect(screen.getByText('idea pending')).toBeTruthy();
    });
    expect(screen.queryByRole('button', { name: /Borrar/i })).toBeNull();
  });

  it('calls twistStore.remove when the delete button is clicked', async () => {
    mockStore.mine = [
      twist({ public_id: 'p', content: 'idea pending', status: 'pending_review' }),
    ];

    render(MyTwistsPanel, { props: { canDelete: true } });
    await waitFor(() => {
      expect(screen.getByText('idea pending')).toBeTruthy();
    });
    await fireEvent.click(
      screen.getByRole('button', { name: /Borrar/i }),
    );
    expect(mockRemove).toHaveBeenCalledWith('p');
  });
});
