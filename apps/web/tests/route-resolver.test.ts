/**
 * Unit tests: route resolver — cycle_state → RouteName.
 *
 * Module 010 / Task T-002.
 *
 * Verifies the FR-002 mapping:
 *   ESTRENO | RECEPCION_IDEAS                     → today
 *   VOTACION                                      → vote
 *   FILTERING | GENERACION | PENDING_RELEASE      → today
 *   FAILED                                        → today
 *   killSwitch.on === true                        → today
 *   cycleState === null (loading)                 → today
 */
import { describe, expect, it } from 'vitest';
import type { CycleState } from '../src/lib/chapter-store.svelte';
import { isPublicRoute, pickRoute } from '../src/lib/route-resolver';

describe('pickRoute', () => {
  it('returns vote when state is VOTACION', () => {
    expect(pickRoute('VOTACION')).toBe('vote');
  });

  it.each<CycleState>([
    'ESTRENO',
    'RECEPCION_IDEAS',
    'FILTERING',
    'GENERACION',
    'PENDING_RELEASE',
    'FAILED',
  ])('returns today when state is %s', (state) => {
    expect(pickRoute(state)).toBe('today');
  });

  it('returns today when cycleState is null (loading)', () => {
    expect(pickRoute(null)).toBe('today');
  });

  it('returns today when kill-switch is on (even mid-VOTACION)', () => {
    expect(pickRoute('VOTACION', { on: true })).toBe('today');
  });

  it('honors cycle state when kill-switch is off', () => {
    expect(pickRoute('VOTACION', { on: false })).toBe('vote');
  });

  it('ignores an absent kill-switch param', () => {
    expect(pickRoute('VOTACION', undefined)).toBe('vote');
  });
});

describe('isPublicRoute', () => {
  it('treats onboarding as public', () => {
    expect(isPublicRoute('onboarding')).toBe(true);
  });

  it.each<'today' | 'vote' | 'me' | 'settings'>([
    'today',
    'vote',
    'me',
    'settings',
  ])('treats %s as private', (r) => {
    expect(isPublicRoute(r)).toBe(false);
  });
});
