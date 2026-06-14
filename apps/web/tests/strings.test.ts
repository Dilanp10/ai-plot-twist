/**
 * Unit tests: Spanish UI strings registry.
 *
 * Module 010 / Task T-001.
 *
 * Sanity-checks the strings tree so a typo (empty value, accidental
 * undefined) is caught at CI, not at runtime in production.
 */
import { describe, expect, it } from 'vitest';
import { S, type Strings } from '../src/lib/strings';

// ---------------------------------------------------------------------------
// Structure
// ---------------------------------------------------------------------------

describe('strings registry shape', () => {
  it('exposes all top-level surfaces', () => {
    const keys = Object.keys(S) as Array<keyof Strings>;
    expect(keys).toEqual(
      expect.arrayContaining([
        'appShell',
        'today',
        'vote',
        'me',
        'settings',
        'install',
        'errors',
        'states',
      ]),
    );
  });

  it('has the four bottom-nav labels', () => {
    expect(S.appShell.nav.today).toBeTruthy();
    expect(S.appShell.nav.vote).toBeTruthy();
    expect(S.appShell.nav.me).toBeTruthy();
    expect(S.appShell.nav.settings).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// No empty / undefined values
// ---------------------------------------------------------------------------

function walkStrings(node: unknown, path: string[] = []): string[] {
  const problems: string[] = [];
  if (typeof node === 'string') {
    if (node.length === 0) {
      problems.push(path.join('.'));
    }
    return problems;
  }
  if (typeof node === 'function') {
    // Cannot deeply validate; just confirm it doesn't throw on a sample input.
    try {
      const out = (node as (...args: unknown[]) => string)(1, 5);
      if (typeof out !== 'string' || out.length === 0) {
        problems.push(`${path.join('.')} (fn returned empty)`);
      }
    } catch {
      problems.push(`${path.join('.')} (fn threw)`);
    }
    return problems;
  }
  if (node !== null && typeof node === 'object') {
    for (const [key, value] of Object.entries(node)) {
      problems.push(...walkStrings(value, [...path, key]));
    }
  }
  return problems;
}

describe('no empty strings', () => {
  it('every leaf value is a non-empty string or non-empty fn', () => {
    const problems = walkStrings(S);
    expect(problems).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Template fns
// ---------------------------------------------------------------------------

describe('template functions', () => {
  it('me.quotaLeft formats both numbers', () => {
    expect(S.me.quotaLeft(2, 3)).toContain('2');
    expect(S.me.quotaLeft(2, 3)).toContain('3');
  });

  it('settings.inviteCodeMasked masks all but last 4', () => {
    expect(S.settings.inviteCodeMasked('A1B2')).toContain('A1B2');
    expect(S.settings.inviteCodeMasked('A1B2')).toContain('•');
  });

  it('settings.appVersion includes the version', () => {
    expect(S.settings.appVersion('0.1.0')).toContain('0.1.0');
  });

  it('states.firstReleaseHint includes the timestamp', () => {
    expect(S.states.firstReleaseHint('12:00')).toContain('12:00');
  });
});
