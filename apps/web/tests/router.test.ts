/**
 * Unit tests: SPA router.
 *
 * Module 002 / Task T-022.
 *
 * Tests the router module in isolation — no Svelte components involved.
 * The router uses module-level $state, so _resetRoute() is called between
 * tests to guarantee isolation.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { _resetRoute, router } from '../src/lib/router.svelte';

beforeEach(() => {
  _resetRoute();
});

afterEach(() => {
  _resetRoute();
  // Clear any hash set during tests
  if (typeof window !== 'undefined') {
    window.location.hash = '';
  }
});

// ---------------------------------------------------------------------------
// Initial state
// ---------------------------------------------------------------------------

describe('router.current', () => {
  it('starts at / after reset', () => {
    expect(router.current).toBe('/');
  });
});

// ---------------------------------------------------------------------------
// router.navigate
// ---------------------------------------------------------------------------

describe('router.navigate', () => {
  it('updates current to the given path', () => {
    router.navigate('/today');
    expect(router.current).toBe('/today');
  });

  it('updates current to /onboarding', () => {
    router.navigate('/onboarding');
    expect(router.current).toBe('/onboarding');
  });

  it('replaces one route with another', () => {
    router.navigate('/today');
    router.navigate('/onboarding');
    expect(router.current).toBe('/onboarding');
  });

  it('sets window.location.hash to the path', () => {
    router.navigate('/today');
    expect(window.location.hash).toBe('#/today');
  });
});

// ---------------------------------------------------------------------------
// router.syncFromHash
// ---------------------------------------------------------------------------

describe('router.syncFromHash', () => {
  it('reads the current hash and updates current', () => {
    window.location.hash = '/today';
    router.syncFromHash();
    expect(router.current).toBe('/today');
  });

  it('falls back to / when hash is empty', () => {
    window.location.hash = '';
    router.syncFromHash();
    expect(router.current).toBe('/');
  });
});

// ---------------------------------------------------------------------------
// _resetRoute
// ---------------------------------------------------------------------------

describe('_resetRoute', () => {
  it('resets to / by default', () => {
    router.navigate('/today');
    _resetRoute();
    expect(router.current).toBe('/');
  });

  it('resets to a specified path', () => {
    router.navigate('/today');
    _resetRoute('/onboarding');
    expect(router.current).toBe('/onboarding');
  });
});
