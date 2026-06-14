/**
 * Unit tests: SW update notifier store (T-008).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  _resetSwUpdate,
  reportSwUpdateAvailable,
  swUpdate,
} from '../src/lib/sw-update-notifier.svelte';

beforeEach(() => {
  _resetSwUpdate();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('swUpdate store', () => {
  it('needRefresh starts false', () => {
    expect(swUpdate.needRefresh).toBe(false);
  });

  it('reportSwUpdateAvailable flips needRefresh', () => {
    reportSwUpdateAvailable(async () => {});
    expect(swUpdate.needRefresh).toBe(true);
  });

  it('dismiss() clears needRefresh', () => {
    reportSwUpdateAvailable(async () => {});
    swUpdate.dismiss();
    expect(swUpdate.needRefresh).toBe(false);
  });

  it('applyAndReload calls the activate callback then reloads', async () => {
    const activate = vi.fn().mockResolvedValue(undefined);
    const reload = vi.fn();
    vi.stubGlobal('location', { reload });

    reportSwUpdateAvailable(activate);
    await swUpdate.applyAndReload();

    expect(activate).toHaveBeenCalledOnce();
    expect(reload).toHaveBeenCalledOnce();

    vi.unstubAllGlobals();
  });

  it('applyAndReload is a no-op when no update is pending', async () => {
    const reload = vi.fn();
    vi.stubGlobal('location', { reload });

    await swUpdate.applyAndReload();
    expect(reload).not.toHaveBeenCalled();

    vi.unstubAllGlobals();
  });
});
