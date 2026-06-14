/**
 * Component tests: AppShell + TopBar + BottomNav.
 *
 * Module 010 / Task T-003.
 *
 * Covers:
 *   - BottomNav renders 4 tabs with labels from strings.ts.
 *   - BottomNav marks the active tab with aria-current="page".
 *   - BottomNav tapping a tab calls router.navigate.
 *   - TopBar hides cycle-state meta when chapter store is loading.
 *   - TopBar shows day + state badge when store.status === 'ok'.
 *   - AppShell renders the slot content between top and bottom.
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import AppShell from '../src/lib/components/AppShell.svelte';
import BottomNav from '../src/lib/components/BottomNav.svelte';
import TopBar from '../src/lib/components/TopBar.svelte';
import { S } from '../src/lib/strings';

// ---------------------------------------------------------------------------
// router mock — captures navigate() calls
// ---------------------------------------------------------------------------

const { routerMock } = vi.hoisted(() => ({
  routerMock: {
    current: '/today',
    navigate: vi.fn(),
    syncFromHash: vi.fn(),
  },
}));

vi.mock('../src/lib/router.svelte', () => ({
  router: routerMock,
  _resetRoute: vi.fn(),
}));

// ---------------------------------------------------------------------------
// chapterStore mock — TopBar reads cycle_state from it
// ---------------------------------------------------------------------------

interface MockStore {
  status: string;
  data: null | {
    cycle_state: string;
    chapter: { day_index: number };
  };
}

const { storeMock } = vi.hoisted(
  (): { storeMock: MockStore } => ({
    storeMock: { status: 'loading', data: null },
  }),
);

vi.mock('../src/lib/chapter-store.svelte', () => ({
  chapterStore: storeMock,
}));

// ---------------------------------------------------------------------------
// Test setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  routerMock.current = '/today';
  routerMock.navigate.mockClear();
  storeMock.status = 'loading';
  storeMock.data = null;
});

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------
// BottomNav
// ---------------------------------------------------------------------------

describe('BottomNav', () => {
  it('renders the four nav labels from strings.ts', () => {
    render(BottomNav);
    expect(screen.getByText(S.appShell.nav.today)).toBeTruthy();
    expect(screen.getByText(S.appShell.nav.vote)).toBeTruthy();
    expect(screen.getByText(S.appShell.nav.me)).toBeTruthy();
    expect(screen.getByText(S.appShell.nav.settings)).toBeTruthy();
  });

  it('marks the active tab with aria-current="page"', () => {
    routerMock.current = '/vote';
    render(BottomNav);
    const voteTab = screen.getByText(S.appShell.nav.vote).closest('a');
    expect(voteTab?.getAttribute('aria-current')).toBe('page');

    const todayTab = screen.getByText(S.appShell.nav.today).closest('a');
    expect(todayTab?.getAttribute('aria-current')).toBeNull();
  });

  it('navigates on tab click', async () => {
    render(BottomNav);
    const voteTab = screen.getByText(S.appShell.nav.vote).closest('a');
    await fireEvent.click(voteTab!);
    expect(routerMock.navigate).toHaveBeenCalledWith('/vote');
  });

  it('uses a <nav> landmark with a Spanish aria-label', () => {
    render(BottomNav);
    const nav = screen.getByRole('navigation');
    expect(nav.getAttribute('aria-label')).toBe('Navegación principal');
  });
});

// ---------------------------------------------------------------------------
// TopBar
// ---------------------------------------------------------------------------

describe('TopBar', () => {
  it('renders the app name', () => {
    render(TopBar);
    expect(screen.getByText(S.appShell.appName)).toBeTruthy();
  });

  it('does not render day / badge when status !== ok', () => {
    storeMock.status = 'loading';
    render(TopBar);
    expect(screen.queryByText(/Día/)).toBeNull();
  });

  it('renders day index + badge when status === ok', () => {
    storeMock.status = 'ok';
    storeMock.data = {
      cycle_state: 'VOTACION',
      chapter: { day_index: 7 },
    };
    render(TopBar);
    expect(screen.getByText('Día 7')).toBeTruthy();
    expect(screen.getByText(S.states.votacion)).toBeTruthy();
  });

  it('uses the PENDING_RELEASE label for that state', () => {
    storeMock.status = 'ok';
    storeMock.data = {
      cycle_state: 'PENDING_RELEASE',
      chapter: { day_index: 3 },
    };
    render(TopBar);
    expect(screen.getByText(S.states.pendingRelease)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// AppShell
// ---------------------------------------------------------------------------

describe('AppShell', () => {
  it('renders TopBar + BottomNav around its content', () => {
    render(AppShell);
    // Brand from TopBar + nav landmark from BottomNav must both be there.
    expect(screen.getByText(S.appShell.appName)).toBeTruthy();
    expect(screen.getByRole('navigation')).toBeTruthy();
  });

  it('exposes a <main> landmark for the content slot', () => {
    render(AppShell);
    expect(screen.getByRole('main')).toBeTruthy();
  });
});
