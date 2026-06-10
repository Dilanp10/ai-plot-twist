/**
 * Unit tests: window-countdown selection + formatting.
 *
 * Module 004 / Task T-013.
 */

import { describe, expect, it } from 'vitest';
import { formatRemaining, windowFor } from '../src/lib/window-countdown';
import type { Windows } from '../src/lib/chapter-store.svelte';

const WINDOWS: Windows = {
  submit_until: '2026-06-09T21:00:00Z',
  vote_from: '2026-06-09T21:00:00Z',
  vote_until: '2026-06-10T02:00:00Z',
  next_release: '2026-06-10T15:00:00Z',
};

// ---------------------------------------------------------------------------
// windowFor — one assertion per FSM state (7 states)
// ---------------------------------------------------------------------------

describe('windowFor() per cycle state', () => {
  it('PENDING_RELEASE → next_release', () => {
    const r = windowFor('PENDING_RELEASE', WINDOWS);
    expect(r.label).toBe('Próximo capítulo');
    expect(r.target.toISOString()).toBe('2026-06-10T15:00:00.000Z');
  });

  it('ESTRENO → submit_until', () => {
    const r = windowFor('ESTRENO', WINDOWS);
    expect(r.label).toBe('Cierra la ronda de ideas');
    expect(r.target.toISOString()).toBe('2026-06-09T21:00:00.000Z');
  });

  it('RECEPCION_IDEAS → submit_until', () => {
    const r = windowFor('RECEPCION_IDEAS', WINDOWS);
    expect(r.label).toBe('Cierra la ronda de ideas');
    expect(r.target.toISOString()).toBe('2026-06-09T21:00:00.000Z');
  });

  it('FILTERING → vote_until', () => {
    const r = windowFor('FILTERING', WINDOWS);
    expect(r.label).toBe('Cierra la votación');
    expect(r.target.toISOString()).toBe('2026-06-10T02:00:00.000Z');
  });

  it('VOTACION → vote_until', () => {
    const r = windowFor('VOTACION', WINDOWS);
    expect(r.label).toBe('Cierra la votación');
    expect(r.target.toISOString()).toBe('2026-06-10T02:00:00.000Z');
  });

  it('GENERACION → next_release', () => {
    const r = windowFor('GENERACION', WINDOWS);
    expect(r.label).toBe('Próximo capítulo');
    expect(r.target.toISOString()).toBe('2026-06-10T15:00:00.000Z');
  });

  it('FAILED → next_release', () => {
    const r = windowFor('FAILED', WINDOWS);
    expect(r.label).toBe('Próximo capítulo');
    expect(r.target.toISOString()).toBe('2026-06-10T15:00:00.000Z');
  });
});

// ---------------------------------------------------------------------------
// formatRemaining
// ---------------------------------------------------------------------------

describe('formatRemaining', () => {
  it('shows hours+minutes+seconds when over 1 hour out', () => {
    const target = new Date('2026-06-09T15:01:02Z');
    const now = new Date('2026-06-09T14:00:00Z');
    expect(formatRemaining(target, now)).toBe('1h 1m 2s');
  });

  it('omits the hours segment when under 1 hour out', () => {
    const target = new Date('2026-06-09T14:05:30Z');
    const now = new Date('2026-06-09T14:00:00Z');
    expect(formatRemaining(target, now)).toBe('5m 30s');
  });

  it('omits hours and minutes when under 1 minute out', () => {
    const target = new Date('2026-06-09T14:00:42Z');
    const now = new Date('2026-06-09T14:00:00Z');
    expect(formatRemaining(target, now)).toBe('42s');
  });

  it('returns "Cerrado" when target has passed', () => {
    const target = new Date('2026-06-09T13:00:00Z');
    const now = new Date('2026-06-09T14:00:00Z');
    expect(formatRemaining(target, now)).toBe('Cerrado');
  });

  it('returns "Cerrado" at the exact target instant', () => {
    const t = new Date('2026-06-09T14:00:00Z');
    expect(formatRemaining(t, t)).toBe('Cerrado');
  });
});

// ---------------------------------------------------------------------------
// Round-trip with chapter-store types
// ---------------------------------------------------------------------------

describe('integration with Windows type', () => {
  it('accepts the same Windows shape served by /chapters/today', () => {
    const r = windowFor('RECEPCION_IDEAS', WINDOWS);
    expect(r.target instanceof Date).toBe(true);
    expect(Number.isNaN(r.target.getTime())).toBe(false);
  });
});
