/**
 * Component a11y tests via axe-core (T-013).
 *
 * Module 010 / Task T-013.
 *
 * Runs WCAG 2.0/2.1/2.2 A+AA rules against each PWA-shell component
 * rendered with realistic props. This is the fast in-jsdom gate;
 * a full keyboard / focus-flow e2e remains for Playwright (out of
 * scope here — covered by the eventual Lighthouse a11y pass).
 *
 * If axe-core flags a violation, the test prints the rule id, the
 * impact, the help URL, and the offending HTML so the failure is
 * actionable in CI logs.
 */
import { cleanup, render } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, it, vi } from 'vitest';

import AppShell from '../src/lib/components/AppShell.svelte';
import BottomNav from '../src/lib/components/BottomNav.svelte';
import ErrorBoundary from '../src/lib/components/ErrorBoundary.svelte';
import InstallPromptCard from '../src/lib/components/InstallPromptCard.svelte';
import IosInstallSheet from '../src/lib/components/IosInstallSheet.svelte';
import Skeleton from '../src/lib/components/Skeleton.svelte';
import SwUpdateToast from '../src/lib/components/SwUpdateToast.svelte';
import TopBar from '../src/lib/components/TopBar.svelte';
import { expectNoA11yViolations } from './_axe-helpers';

// ---------------------------------------------------------------------------
// Mocks for stateful imports so each component renders deterministically.
// ---------------------------------------------------------------------------

vi.mock('../src/lib/router.svelte', () => ({
  router: {
    current: '/today',
    navigate: vi.fn(),
    syncFromHash: vi.fn(),
  },
  _resetRoute: vi.fn(),
}));

const { chapterStore } = vi.hoisted(() => ({
  chapterStore: {
    status: 'ok',
    data: {
      cycle_state: 'VOTACION',
      chapter: { day_index: 7 },
    },
  },
}));

vi.mock('../src/lib/chapter-store.svelte', () => ({ chapterStore }));

vi.mock('../src/lib/install-prompt.svelte', () => ({
  installPrompt: {
    canPrompt: true,
    prompt: vi.fn().mockResolvedValue('accepted'),
    dismiss: vi.fn(),
  },
  installBeforeInstallPromptListener: vi.fn(),
  _resetInstallPrompt: vi.fn(),
  _setInstallPrompt: vi.fn(),
}));

vi.mock('../src/lib/sw-update-notifier.svelte', () => ({
  swUpdate: {
    needRefresh: true,
    applyAndReload: vi.fn().mockResolvedValue(undefined),
    dismiss: vi.fn(),
  },
  reportSwUpdateAvailable: vi.fn(),
  _resetSwUpdate: vi.fn(),
}));

vi.mock('../src/lib/ios-install-sheet', () => ({
  detectIosInstallState: () => 'show_instructions',
}));

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  // sessionStorage influences InstallPromptCard / IosInstallSheet visibility.
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
});

describe('a11y — Skeleton', () => {
  it('has no axe violations', async () => {
    const { container } = render(Skeleton);
    await expectNoA11yViolations(container);
  });
});

describe('a11y — TopBar', () => {
  it('has no axe violations with data loaded', async () => {
    const { container } = render(TopBar);
    await expectNoA11yViolations(container);
  });
});

describe('a11y — BottomNav', () => {
  it('has no axe violations', async () => {
    const { container } = render(BottomNav);
    await expectNoA11yViolations(container);
  });
});

describe('a11y — AppShell', () => {
  it('has no axe violations', async () => {
    const { container } = render(AppShell);
    await expectNoA11yViolations(container);
  });
});

describe('a11y — ErrorBoundary (happy path)', () => {
  it('has no axe violations in the non-error state', async () => {
    const { container } = render(ErrorBoundary);
    await expectNoA11yViolations(container);
  });
});

describe('a11y — InstallPromptCard', () => {
  it('has no axe violations when shown', async () => {
    const { container } = render(InstallPromptCard);
    await expectNoA11yViolations(container);
  });
});

describe('a11y — IosInstallSheet', () => {
  it('has no axe violations in show_instructions state', async () => {
    const { container } = render(IosInstallSheet);
    await expectNoA11yViolations(container);
  });
});

describe('a11y — SwUpdateToast', () => {
  it('has no axe violations when shown', async () => {
    const { container } = render(SwUpdateToast);
    await expectNoA11yViolations(container);
  });
});
