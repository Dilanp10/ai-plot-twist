/**
 * Component tests for VoteCard + MyVotesIndicator (T-011).
 *
 * VoteCard delegates the vote action to a parent callback so we just
 * mock the onvote prop. MyVotesIndicator reads voteStore directly, so we
 * mock it.
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import MyVotesIndicator from '../src/lib/components/MyVotesIndicator.svelte';
import VoteCard from '../src/lib/components/VoteCard.svelte';
import type { FeedItem } from '../src/lib/vote-api';

// ---------------------------------------------------------------------------
// voteStore mock (for MyVotesIndicator)
// ---------------------------------------------------------------------------

const { mockStore } = vi.hoisted(() => ({
  mockStore: {
    quota: { used: 0, max: 5, remaining: 5 },
  },
}));

vi.mock('../src/lib/vote-store.svelte', () => ({ voteStore: mockStore }));

const TWIST_ID = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa';

function item(overrides: Partial<FeedItem> = {}): FeedItem {
  return {
    id: TWIST_ID,
    content: 'Una idea brillante',
    vote_count: 3,
    has_my_vote: false,
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// VoteCard
// ---------------------------------------------------------------------------

describe('VoteCard', () => {
  it('renders content + vote_count + a Votar button', () => {
    render(VoteCard, { props: { item: item(), onvote: vi.fn() } });
    expect(screen.getByText('Una idea brillante')).toBeTruthy();
    expect(screen.getByText(/👍 3/)).toBeTruthy();
    expect(screen.getByRole('button', { name: /votar/i })).toBeTruthy();
  });

  it('shows "Ya votaste" + disables button when has_my_vote=true', () => {
    render(VoteCard, {
      props: { item: item({ has_my_vote: true }), onvote: vi.fn() },
    });
    const btn = screen.getByRole('button') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.textContent).toMatch(/ya votaste/i);
    expect(btn.getAttribute('aria-pressed')).toBe('true');
  });

  it('calls onvote with the twist id when clicked', async () => {
    const onvote = vi.fn();
    render(VoteCard, { props: { item: item(), onvote } });
    await fireEvent.click(screen.getByRole('button'));
    expect(onvote).toHaveBeenCalledWith(TWIST_ID);
  });

  it('disables the button when disabled=true even if not yet voted', () => {
    render(VoteCard, {
      props: { item: item(), disabled: true, onvote: vi.fn() },
    });
    const btn = screen.getByRole('button') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it('does not call onvote when disabled', async () => {
    const onvote = vi.fn();
    render(VoteCard, {
      props: { item: item(), disabled: true, onvote },
    });
    await fireEvent.click(screen.getByRole('button'));
    expect(onvote).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// MyVotesIndicator
// ---------------------------------------------------------------------------

describe('MyVotesIndicator', () => {
  beforeEach(() => {
    mockStore.quota = { used: 0, max: 5, remaining: 5 };
  });

  it('renders max dots, used filled', () => {
    mockStore.quota = { used: 2, max: 5, remaining: 3 };
    const { container } = render(MyVotesIndicator);
    const dots = container.querySelectorAll('.dot');
    expect(dots).toHaveLength(5);
    const filled = container.querySelectorAll('.dot.filled');
    expect(filled).toHaveLength(2);
  });

  it('renders the count as "used / max"', () => {
    mockStore.quota = { used: 3, max: 5, remaining: 2 };
    render(MyVotesIndicator);
    expect(screen.getByText('3 / 5')).toBeTruthy();
  });

  it('has an a11y label exposing the count', () => {
    mockStore.quota = { used: 1, max: 5, remaining: 4 };
    const { container } = render(MyVotesIndicator);
    const root = container.querySelector('.indicator');
    expect(root?.getAttribute('aria-label')).toMatch(/1 de 5/);
  });
});
