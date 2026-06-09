/**
 * Invite-code input mask.
 *
 * Module 002 / Task T-021.
 *
 * Formats a raw string into canonical `XXXX-XXXX` as the user types.
 * - Strips any character not in the Base32 alphabet (A–Z, 2–7)
 * - Uppercases the input
 * - Inserts a hyphen after the 4th character
 * - Truncates to 9 characters (4 + hyphen + 4)
 *
 * Designed to be called on every `input` event:
 *   code = maskInviteCode(event.currentTarget.value)
 */

const NON_BASE32 = /[^A-Z2-7]/g;

/**
 * Return the canonical invite-code form of *raw*, suitable for display
 * inside a controlled input.
 *
 * @example
 *   maskInviteCode('abcde')      // → 'ABCD-E'
 *   maskInviteCode('ABCDEFGH')   // → 'ABCD-EFGH'
 *   maskInviteCode('ABCD-EFGH')  // → 'ABCD-EFGH'  (idempotent)
 */
export function maskInviteCode(raw: string): string {
  const clean = raw.toUpperCase().replace(/-/g, '').replace(NON_BASE32, '');
  const capped = clean.slice(0, 8);
  return capped.length <= 4 ? capped : `${capped.slice(0, 4)}-${capped.slice(4)}`;
}
