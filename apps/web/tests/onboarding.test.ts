/**
 * Unit tests: Onboarding screen.
 *
 * Module 002 / Task T-021.
 *
 * Mocks apiFetch so no HTTP calls are made.
 * Uses @testing-library/svelte to render and interact with the component.
 */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Onboarding from '../src/routes/onboarding.svelte';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const { mockApiFetch } = vi.hoisted(() => {
  const mockApiFetch = vi.fn();
  return { mockApiFetch };
});

vi.mock('../src/lib/api', () => ({ apiFetch: mockApiFetch }));

vi.mock('../src/lib/auth-store.svelte', () => ({
  authStore: {
    jwt: null,
    setSession: vi.fn().mockResolvedValue(undefined),
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

describe('renders correctly', () => {
  it('shows invite-code and display-name inputs', () => {
    render(Onboarding);
    expect(screen.getByLabelText(/código de invitación/i)).toBeTruthy();
    expect(screen.getByLabelText(/nombre en el juego/i)).toBeTruthy();
  });

  it('submit button is disabled when inputs are empty', () => {
    render(Onboarding);
    const btn = screen.getByRole('button', { name: /ingresar/i });
    expect((btn as HTMLButtonElement).disabled).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Code input mask
// ---------------------------------------------------------------------------

describe('invite-code auto-mask', () => {
  it('formats input to XXXX-XXXX as user types', async () => {
    render(Onboarding);
    const input = screen.getByLabelText(/código de invitación/i) as HTMLInputElement;

    await fireEvent.input(input, { target: { value: 'abcdefgh' } });

    expect(input.value).toBe('ABCD-EFGH');
  });

  it('strips non-Base32 characters', async () => {
    render(Onboarding);
    const input = screen.getByLabelText(/código de invitación/i) as HTMLInputElement;

    await fireEvent.input(input, { target: { value: 'ABCD-1234' } });

    // 1, 8, 9, 0 are not valid Base32; 2,3,4,5,6,7 are.
    // '1','8','9','0' → stripped; 'A','B','C','D','2','3','4' remain
    expect(input.value).toBe('ABCD-234');
  });
});

// ---------------------------------------------------------------------------
// Form submission — happy path
// ---------------------------------------------------------------------------

describe('successful submission', () => {
  it('dispatches auth:navigate to /today on success', async () => {
    mockApiFetch.mockResolvedValue({
      ok: true,
      data: {
        jwt: 'new.jwt',
        device_secret: 'new-secret',
        jwt_expires_at: '2026-09-01T00:00:00Z',
        user: {
          public_id: 'uuid-1234',
          display_name: 'NuevoJugador',
          created_at: '2026-06-08T00:00:00Z',
          last_seen_at: '2026-06-08T00:00:00Z',
        },
      },
    });

    const navigateEvents: CustomEvent[] = [];
    window.addEventListener('auth:navigate', (e) =>
      navigateEvents.push(e as CustomEvent),
    );

    render(Onboarding);
    const codeInput = screen.getByLabelText(/código de invitación/i) as HTMLInputElement;
    const nameInput = screen.getByLabelText(/nombre en el juego/i) as HTMLInputElement;

    await fireEvent.input(codeInput, { target: { value: 'ABCDEFGH' } });
    await fireEvent.input(nameInput, { target: { value: 'NuevoJugador' } });
    await fireEvent.submit(codeInput.closest('form')!);

    await waitFor(() => {
      expect(navigateEvents.length).toBe(1);
    });
    expect(navigateEvents[0]!.detail).toEqual({ path: '/today' });

    window.removeEventListener('auth:navigate', (e) =>
      navigateEvents.push(e as CustomEvent),
    );
  });
});

// ---------------------------------------------------------------------------
// Error messages
// ---------------------------------------------------------------------------

describe('error messages', () => {
  const cases: Array<[number, RegExp]> = [
    [404, /ese código no anda/i],
    [409, /probá con otro nombre/i],
    [422, /revisá el nombre/i],
    [429, /demasiados intentos/i],
  ];

  for (const [status, pattern] of cases) {
    it(`shows correct message for ${status}`, async () => {
      mockApiFetch.mockResolvedValue({ ok: false, status, body: {} });

      render(Onboarding);
      const codeInput = screen.getByLabelText(/código de invitación/i) as HTMLInputElement;
      const nameInput = screen.getByLabelText(/nombre en el juego/i) as HTMLInputElement;

      await fireEvent.input(codeInput, { target: { value: 'ABCDEFGH' } });
      await fireEvent.input(nameInput, { target: { value: 'Alguien' } });
      await fireEvent.submit(codeInput.closest('form')!);

      await waitFor(() => {
        expect(screen.getByRole('alert').textContent).toMatch(pattern);
      });
    });
  }
});
