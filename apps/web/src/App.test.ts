/**
 * Tests for the root App component — boot routing logic.
 *
 * Module 002 / Task T-022.
 *
 * Mocks authStore so no real IndexedDB is touched.
 * Uses the real router module (but resets state between tests via _resetRoute).
 */
import { cleanup, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App.svelte';
import { chapterStore } from './lib/chapter-store.svelte';
import { _resetRoute } from './lib/router.svelte';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const { mockAuthStore, mockApiFetch } = vi.hoisted(() => {
  const mockAuthStore = {
    jwt: null as string | null,
    user: null as { display_name: string } | null,
    init: vi.fn<() => Promise<void>>().mockResolvedValue(undefined),
    clear: vi.fn<() => Promise<void>>().mockResolvedValue(undefined),
    setSession: vi.fn().mockResolvedValue(undefined),
    updateJwt: vi.fn().mockResolvedValue(undefined),
  };
  const mockApiFetch = vi.fn();
  return { mockAuthStore, mockApiFetch };
});

vi.mock('./lib/auth-store.svelte', () => ({ authStore: mockAuthStore }));

// Onboarding (submit) and chapter-store (today mount) both go through apiFetch.
// Default to a benign no_active_season so the today route lands on the
// "La historia todavía no empezó" empty-state — no TodayResponse fixture needed.
vi.mock('./lib/api', () => ({ apiFetch: mockApiFetch }));

// ---------------------------------------------------------------------------
// Setup / teardown
// ---------------------------------------------------------------------------

beforeEach(() => {
  _resetRoute();
  chapterStore._reset();
  mockAuthStore.jwt = null;
  mockAuthStore.user = null;
  mockAuthStore.init.mockResolvedValue(undefined);
  mockApiFetch.mockResolvedValue({
    ok: false,
    status: 503,
    body: { code: 'no_active_season' },
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('App boot routing', () => {
  it('mounts without throwing', () => {
    expect(() => render(App)).not.toThrow();
  });

  it('shows onboarding when no JWT is present', async () => {
    mockAuthStore.jwt = null;
    render(App);

    await waitFor(() => {
      expect(screen.getByLabelText(/código de invitación/i)).toBeTruthy();
    });
  });

  it('shows today page when JWT is present', async () => {
    mockAuthStore.jwt = 'existing.jwt.token';
    render(App);

    await waitFor(() => {
      expect(screen.getByText(/la historia todavía no empezó/i)).toBeTruthy();
    });
  });

  it('navigates to /today when auth:navigate event is dispatched', async () => {
    mockAuthStore.jwt = null;
    render(App);

    // Wait for onboarding to appear first
    await waitFor(() => {
      expect(screen.getByLabelText(/código de invitación/i)).toBeTruthy();
    });

    // Simulate successful login dispatching the navigation event
    window.dispatchEvent(
      new CustomEvent('auth:navigate', { detail: { path: '/today' } }),
    );

    await waitFor(() => {
      expect(screen.getByText(/la historia todavía no empezó/i)).toBeTruthy();
    });
  });

  it('navigates to /onboarding when auth:logout event is dispatched', async () => {
    mockAuthStore.jwt = 'existing.jwt.token';
    render(App);

    await waitFor(() => {
      expect(screen.getByText(/la historia todavía no empezó/i)).toBeTruthy();
    });

    window.dispatchEvent(new CustomEvent('auth:logout'));

    await waitFor(() => {
      expect(screen.getByLabelText(/código de invitación/i)).toBeTruthy();
    });
  });
});
