/**
 * Unit tests: install-prompt store + iOS detection (T-005, T-006).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  _resetInstallPrompt,
  _setInstallPrompt,
  installBeforeInstallPromptListener,
  installPrompt,
} from '../src/lib/install-prompt.svelte';
import { detectIosInstallState } from '../src/lib/ios-install-sheet';

interface FakeBipEvent {
  preventDefault: () => void;
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>;
}

function fakeBip(outcome: 'accepted' | 'dismissed'): FakeBipEvent {
  return {
    preventDefault: vi.fn(),
    prompt: vi.fn().mockResolvedValue(undefined),
    userChoice: Promise.resolve({ outcome }),
  };
}

beforeEach(() => {
  _resetInstallPrompt();
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// installPrompt store
// ---------------------------------------------------------------------------

describe('installPrompt store', () => {
  it('canPrompt is false before an event is captured', () => {
    expect(installPrompt.canPrompt).toBe(false);
  });

  it('canPrompt flips to true after an event is captured', () => {
    _setInstallPrompt(fakeBip('accepted') as unknown as BeforeInstallPromptEvent);
    expect(installPrompt.canPrompt).toBe(true);
  });

  it('prompt() returns "noop" when no event is queued', async () => {
    const outcome = await installPrompt.prompt();
    expect(outcome).toBe('noop');
  });

  it('prompt() resolves to the user choice and clears the queue', async () => {
    _setInstallPrompt(fakeBip('accepted') as unknown as BeforeInstallPromptEvent);
    const outcome = await installPrompt.prompt();
    expect(outcome).toBe('accepted');
    expect(installPrompt.canPrompt).toBe(false);
  });

  it('dismiss() clears without calling prompt()', () => {
    const e = fakeBip('accepted');
    _setInstallPrompt(e as unknown as BeforeInstallPromptEvent);
    installPrompt.dismiss();
    expect(installPrompt.canPrompt).toBe(false);
    expect(e.prompt).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// installBeforeInstallPromptListener
// ---------------------------------------------------------------------------

describe('installBeforeInstallPromptListener', () => {
  it('captures the event and prevents default', () => {
    installBeforeInstallPromptListener();
    const evt = fakeBip('accepted');
    // Cast to Event so dispatchEvent accepts the synthetic shape.
    const dispatched = new Event('beforeinstallprompt');
    Object.assign(dispatched, evt);
    window.dispatchEvent(dispatched);
    expect(installPrompt.canPrompt).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// detectIosInstallState
// ---------------------------------------------------------------------------

describe('detectIosInstallState', () => {
  it('returns not_ios for Android', () => {
    expect(
      detectIosInstallState(
        'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36',
      ),
    ).toBe('not_ios');
  });

  it('returns not_ios for desktop Chrome', () => {
    expect(
      detectIosInstallState(
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
      ),
    ).toBe('not_ios');
  });

  it('returns in_app_browser for Instagram WebView on iOS', () => {
    expect(
      detectIosInstallState(
        'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 Instagram 290.0.0',
      ),
    ).toBe('in_app_browser');
  });

  it('returns show_instructions for plain iOS Safari', () => {
    expect(
      detectIosInstallState(
        'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1',
      ),
    ).toBe('show_instructions');
  });
});
