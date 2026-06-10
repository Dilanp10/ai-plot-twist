/**
 * Per-FSM-state countdown selection.
 *
 * Module 004 / Task T-013.
 *
 * Decides which of the four window timestamps the /today screen should be
 * counting down to, based on the current cycle state. The result is a
 * label (for the badge above the countdown) and a target Date (UTC).
 *
 * The mapping is intentionally pure and synchronous — the calling
 * component can call ``windowFor()`` once per render with no I/O.
 *
 * State → target window mapping (per spec User Story 1):
 *
 *   PENDING_RELEASE  → next_release   ("Próximo capítulo")
 *   ESTRENO          → submit_until   ("Cierra la ronda de ideas")
 *   RECEPCION_IDEAS  → submit_until   ("Cierra la ronda de ideas")
 *   FILTERING        → vote_until     ("Cierra la votación")
 *   VOTACION         → vote_until     ("Cierra la votación")
 *   GENERACION       → next_release   ("Próximo capítulo")
 *   FAILED           → next_release   ("Próximo capítulo")  — frozen by 003
 */

import type { CycleState, Windows } from './chapter-store.svelte';

export interface CountdownTarget {
  label: string;
  target: Date;
}

const SUBMIT_LABEL = 'Cierra la ronda de ideas';
const VOTE_LABEL = 'Cierra la votación';
const RELEASE_LABEL = 'Próximo capítulo';

export function windowFor(state: CycleState, windows: Windows): CountdownTarget {
  switch (state) {
    case 'ESTRENO':
    case 'RECEPCION_IDEAS':
      return { label: SUBMIT_LABEL, target: new Date(windows.submit_until) };
    case 'FILTERING':
    case 'VOTACION':
      return { label: VOTE_LABEL, target: new Date(windows.vote_until) };
    case 'PENDING_RELEASE':
    case 'GENERACION':
    case 'FAILED':
      return { label: RELEASE_LABEL, target: new Date(windows.next_release) };
  }
}

/**
 * Format the remaining time to *target* as a localized "Xh Ym Zs" string.
 *
 * Returns the literal "Cerrado" when *target* has already passed —
 * the component renders the badge differently in that case.
 */
export function formatRemaining(target: Date, now: Date = new Date()): string {
  const ms = target.getTime() - now.getTime();
  if (ms <= 0) {
    return 'Cerrado';
  }
  const totalSeconds = Math.floor(ms / 1000);
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  if (h > 0) {
    return `${h}h ${m}m ${s}s`;
  }
  if (m > 0) {
    return `${m}m ${s}s`;
  }
  return `${s}s`;
}
