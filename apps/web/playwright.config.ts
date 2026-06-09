/**
 * Playwright configuration for AI Plot Twist Web E2E tests.
 *
 * Module 002 / Task T-023.
 *
 * webServer strategy:
 *   - API server:  started from apps/api via uv, env vars forwarded from shell.
 *   - Web server:  Vite dev server on :5173.
 *   - reuseExistingServer: false in CI (always start fresh); true locally
 *     (reuse if already running — supports running `pnpm dev` manually).
 *
 * Env vars required at runtime:
 *   DATABASE_URL  — PostgreSQL connection string.
 *   JWT_SECRET    — Falls back to a dev-only value when not set.
 */

import path from 'path';
import { defineConfig, devices } from '@playwright/test';

const API_DIR = path.resolve(__dirname, '../api');
const REUSE = !process.env.CI;

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? 'github' : 'list',

  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  webServer: [
    {
      // FastAPI — served from apps/api
      command: 'uv run uvicorn app.main:app --host 0.0.0.0 --port 8000',
      cwd: API_DIR,
      url: 'http://localhost:8000/healthz',
      reuseExistingServer: REUSE,
      timeout: 30_000,
      env: {
        DATABASE_URL: process.env.DATABASE_URL ?? '',
        JWT_SECRET: process.env.JWT_SECRET ?? 'dev-e2e-jwt-secret-min32chars!!',
        TICK_SECRET: process.env.TICK_SECRET ?? 'dev-e2e-tick-secret-placeholder',
        ENV: process.env.ENV ?? 'dev',
      },
    },
    {
      // Vite dev server
      command: 'pnpm dev',
      url: 'http://localhost:5173',
      reuseExistingServer: REUSE,
      timeout: 30_000,
    },
  ],
});
