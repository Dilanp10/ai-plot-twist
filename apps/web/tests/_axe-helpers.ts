/**
 * axe-core helpers for component a11y tests.
 *
 * Module 010 / Task T-013.
 *
 * Runs axe-core against a rendered DOM tree and surfaces any
 * WCAG 2.1 AA violation as a friendly test failure. The PWA targets
 * WCAG 2.2 AA per FR-006, but axe-core's WCAG 2.2 rules ship under
 * the same default ruleset.
 *
 * Usage:
 *   import { renderAxe, expectNoA11yViolations } from './_axe-helpers';
 *
 *   it('has no a11y violations', async () => {
 *     const { container } = render(MyComponent);
 *     await expectNoA11yViolations(container);
 *   });
 */
import axe from 'axe-core';
import { expect } from 'vitest';

/**
 * Run axe-core against the given element and assert zero violations.
 *
 * The default rule set covers WCAG 2.0 / 2.1 A + AA + WCAG 2.2 AA.
 * Failing rules are surfaced with their id, impact, help URL, and
 * the offending node markup — enough to debug in CI logs.
 */
export async function expectNoA11yViolations(
  node: Element,
  options: axe.RunOptions = {},
): Promise<void> {
  const results = await axe.run(node, {
    runOnly: {
      type: 'tag',
      values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'],
    },
    // jsdom doesn't implement HTMLCanvasElement.getContext, which axe-core
    // uses to sample pixels for color-contrast. The rule logs warnings and
    // fails open — turn it off so the test output stays clean. Real contrast
    // is verified at the Lighthouse / browser-level a11y pass instead.
    rules: { 'color-contrast': { enabled: false } },
    ...options,
  });

  if (results.violations.length === 0) return;

  const lines = results.violations.map((v) => {
    const targets = v.nodes
      .map((n) => `      ${n.target.join(' > ')}\n      → ${n.html}`)
      .join('\n');
    return `  [${v.impact ?? 'unknown'}] ${v.id}: ${v.help}\n    ${v.helpUrl}\n${targets}`;
  });

  expect(
    results.violations,
    `axe-core found ${results.violations.length} violation(s):\n${lines.join('\n')}`,
  ).toEqual([]);
}
