/**
 * Client error reporter — throttled queue + global handlers.
 *
 * Module 010 / Task T-011.
 *
 * Sends sanitized error events to ``POST /api/v1/internal/client-log``
 * (T-012). The endpoint is unauthenticated + IP-rate-limited (5/min).
 *
 * Local throttling (FR-010): cap at 10 events per rolling minute so a
 * runaway error loop on the client does not burn the server's bucket.
 *
 * Events come from three sources:
 *   1. ``ErrorBoundary.svelte`` — explicit ``logBoundary()`` call.
 *   2. ``window.error`` listener — synchronous JS errors.
 *   3. ``window.unhandledrejection`` listener — promise rejections.
 *
 * The queue is in-memory only. No persistence across reloads. That's
 * intentional — we only care about live debugging signal.
 */

import { router } from './router.svelte';
import { VERSION as APP_VERSION } from './version';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ClientLogEvent =
  | 'boundary'
  | 'error'
  | 'unhandledrejection'
  | 'csp_violation';

export interface ClientLogPayload {
  event: ClientLogEvent;
  message?: string;
  stack?: string;
  route?: string;
  user_agent: string;
  app_version: string;
  timestamp: string;
}

// ---------------------------------------------------------------------------
// Throttle state — module-scope, intentionally global to this tab
// ---------------------------------------------------------------------------

const _MAX_PER_MINUTE = 10;
const _WINDOW_MS = 60_000;

/** Rolling window of recent send timestamps (ms since epoch). */
const _recent: number[] = [];

function _withinBudget(now: number): boolean {
  while (_recent.length > 0 && now - (_recent[0] as number) > _WINDOW_MS) {
    _recent.shift();
  }
  return _recent.length < _MAX_PER_MINUTE;
}

// ---------------------------------------------------------------------------
// Sanitization
// ---------------------------------------------------------------------------

const _MAX_MESSAGE = 512;
const _MAX_STACK = 2048;

function _truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max) : s;
}

function _safeStack(err: unknown): string | undefined {
  if (err instanceof Error && err.stack) {
    return _truncate(err.stack, _MAX_STACK);
  }
  return undefined;
}

function _safeMessage(err: unknown): string | undefined {
  if (err instanceof Error) return _truncate(err.message, _MAX_MESSAGE);
  if (typeof err === 'string') return _truncate(err, _MAX_MESSAGE);
  return undefined;
}

function _route(): string {
  return router.current || '/';
}

function _userAgent(): string {
  return typeof navigator !== 'undefined'
    ? _truncate(navigator.userAgent, 256)
    : 'unknown';
}

function _now(): string {
  return new Date().toISOString();
}

// ---------------------------------------------------------------------------
// Public surface
// ---------------------------------------------------------------------------

/**
 * Send (or drop, if throttled) a client log event.
 *
 * Failures are silent on purpose: a 429 from the rate-limit, a network
 * outage, an aborted fetch — none of those should surface to the user.
 */
export async function logEvent(payload: ClientLogPayload): Promise<void> {
  const now = Date.now();
  if (!_withinBudget(now)) return;
  _recent.push(now);

  try {
    await fetch('/api/v1/internal/client-log', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      keepalive: true,
    });
  } catch {
    // Swallow: the next event will try again.
  }
}

export interface BoundaryEventInput {
  message?: string;
  stack?: string;
}

export function logBoundary(input: BoundaryEventInput): Promise<void> {
  return logEvent({
    event: 'boundary',
    message: input.message ? _truncate(input.message, _MAX_MESSAGE) : undefined,
    stack: input.stack ? _truncate(input.stack, _MAX_STACK) : undefined,
    route: _route(),
    user_agent: _userAgent(),
    app_version: APP_VERSION,
    timestamp: _now(),
  });
}

function _onError(e: ErrorEvent): void {
  void logEvent({
    event: 'error',
    message: _safeMessage(e.error) ?? _safeMessage(e.message),
    stack: _safeStack(e.error),
    route: _route(),
    user_agent: _userAgent(),
    app_version: APP_VERSION,
    timestamp: _now(),
  });
}

function _onRejection(e: PromiseRejectionEvent): void {
  void logEvent({
    event: 'unhandledrejection',
    message: _safeMessage(e.reason),
    stack: _safeStack(e.reason),
    route: _route(),
    user_agent: _userAgent(),
    app_version: APP_VERSION,
    timestamp: _now(),
  });
}

/** Install ``window.error`` + ``window.unhandledrejection`` handlers. */
export function installGlobalHandlers(): void {
  if (typeof window === 'undefined') return;
  window.addEventListener('error', _onError);
  window.addEventListener('unhandledrejection', _onRejection);
}

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------

/** Clear the rolling window. Tests only. */
export function _resetThrottle(): void {
  _recent.length = 0;
}
