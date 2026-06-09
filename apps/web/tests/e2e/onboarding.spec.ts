/**
 * E2E smoke test: onboarding flow.
 *
 * Module 002 / Task T-023.
 *
 * Prerequisites (handled by Playwright's webServer config):
 *   - API running on :8000 with a real PostgreSQL DB.
 *   - Vite dev server running on :5173.
 *   - DATABASE_URL env var pointing to that DB.
 *
 * The test:
 *   1. Issues a fresh invite code via the CLI.
 *   2. Navigates to the app root.
 *   3. Fills the onboarding form (code + display name).
 *   4. Submits and asserts the user lands on /today.
 *
 * Skips gracefully when DATABASE_URL is not a real DB (e.g. CI placeholder).
 */

import { execSync } from 'child_process';
import path from 'path';
import { expect, test } from '@playwright/test';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Project root (4 levels up from apps/web/tests/e2e). */
const ROOT = path.resolve(__dirname, '../../../..');

/** Re-usable display name for the test user. */
const DISPLAY_NAME = 'JugadorE2E';

// ---------------------------------------------------------------------------
// Skip guard
// ---------------------------------------------------------------------------

const dbUrl = process.env.DATABASE_URL ?? '';
const hasRealDb =
  dbUrl.length > 0 &&
  !dbUrl.includes('__placeholder__') &&
  !dbUrl.includes('__none__');

test.skip(
  !hasRealDb,
  'Skipped: DATABASE_URL is not set or is the CI placeholder.',
);

// ---------------------------------------------------------------------------
// Suite setup — issue a fresh invite code
// ---------------------------------------------------------------------------

let inviteCode = '';

test.beforeAll(() => {
  const output = execSync('pnpm issue-invite --ttl-days 1 --note e2e-smoke', {
    cwd: ROOT,
    env: { ...process.env },
    encoding: 'utf8',
  });

  // The CLI prints a table that contains a line like:
  //   code        XXXX-XXXX
  // Extract the first Base32 code pattern we find.
  const match = output.match(/\b([A-Z2-7]{4}-[A-Z2-7]{4})\b/);
  if (!match) {
    throw new Error(`Could not parse invite code from CLI output:\n${output}`);
  }
  inviteCode = match[1];
});

// ---------------------------------------------------------------------------
// Test
// ---------------------------------------------------------------------------

test('completes onboarding and lands on /today', async ({ page }) => {
  await page.goto('/');

  // Wait for the boot sequence to complete and show onboarding
  const codeInput = page.locator('#invite-code');
  await expect(codeInput).toBeVisible({ timeout: 10_000 });

  // Fill the invite code (the mask runs on input events — fill triggers them)
  await codeInput.fill(inviteCode);
  // After masking, input value should be identical (already in XXXX-XXXX form)
  await expect(codeInput).toHaveValue(inviteCode);

  // Fill the display name
  await page.locator('#display-name').fill(DISPLAY_NAME);

  // Submit button should now be enabled
  const submitBtn = page.locator('button[type="submit"]');
  await expect(submitBtn).not.toBeDisabled();
  await submitBtn.click();

  // Should navigate to /today and show the placeholder text
  await expect(page).toHaveURL(/#\/today$/, { timeout: 10_000 });
  await expect(
    page.getByText(/la historia está por comenzar/i),
  ).toBeVisible({ timeout: 5_000 });
});
