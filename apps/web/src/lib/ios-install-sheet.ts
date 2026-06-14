/**
 * iOS install detection + instruction-sheet gating (T-006).
 *
 * Module 010 / Task T-006.
 *
 * iOS Safari does NOT fire ``beforeinstallprompt``. Users have to
 * tap Share → Add to Home Screen manually. We detect the case where:
 *   - the UA is iOS Safari (NOT an in-app browser like Instagram),
 *   - the app is NOT yet running as a standalone PWA
 *     (``navigator.standalone === false``),
 *
 * and surface :class:`IosInstallSheet`. The "in-app browser" case
 * (Instagram, Twitter) gets a different copy — install is impossible
 * there, so we show a "Abrir en Safari" hint (FR-004 edge case).
 *
 * No reactivity needed: the OS state never changes mid-session, so a
 * one-shot read at mount-time is enough.
 */

export type IosInstallState =
  | 'not_ios' // Android, desktop, etc. — render nothing.
  | 'already_standalone' // PWA installed + opened from home screen.
  | 'in_app_browser' // Instagram / Twitter / etc.; install impossible.
  | 'show_instructions'; // Plain Safari on iOS — show the sheet.

const _IN_APP_BROWSER_TOKENS = [
  'FBAN',
  'FBAV',
  'Instagram',
  'Line',
  'Twitter',
  'TikTok',
  'WhatsApp',
];

function _isIos(ua: string): boolean {
  // iPad on iOS 13+ reports as Mac; gate on touch points too.
  if (/iPad|iPhone|iPod/.test(ua)) return true;
  if (
    typeof navigator !== 'undefined' &&
    /Macintosh/.test(ua) &&
    navigator.maxTouchPoints > 1
  ) {
    return true;
  }
  return false;
}

function _isInAppBrowser(ua: string): boolean {
  return _IN_APP_BROWSER_TOKENS.some((tok) => ua.includes(tok));
}

interface NavigatorWithStandalone extends Navigator {
  standalone?: boolean;
}

function _isStandalone(): boolean {
  if (typeof navigator === 'undefined') return false;
  const nav = navigator as NavigatorWithStandalone;
  if (nav.standalone === true) return true;
  if (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(display-mode: standalone)').matches
  ) {
    return true;
  }
  return false;
}

/** Resolve the install state for the current device + browser. */
export function detectIosInstallState(
  ua: string = typeof navigator !== 'undefined' ? navigator.userAgent : '',
): IosInstallState {
  if (!_isIos(ua)) return 'not_ios';
  if (_isStandalone()) return 'already_standalone';
  if (_isInAppBrowser(ua)) return 'in_app_browser';
  return 'show_instructions';
}
