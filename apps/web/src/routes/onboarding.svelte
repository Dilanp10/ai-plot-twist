<script lang="ts">
  /**
   * Onboarding screen — invite code + display name → redeem → /today.
   *
   * Module 002 / Task T-021.
   */
  import { apiFetch } from '../lib/api';
  import { authStore, type PublicUser } from '../lib/auth-store.svelte';
  import { maskInviteCode } from '../lib/code-input-mask';

  // Form state
  let code = $state('');
  let displayName = $state('');
  let error = $state<string | null>(null);
  let loading = $state(false);

  // Response shape from POST /api/v1/auth/redeem-invite
  interface RedeemResponse {
    jwt: string;
    device_secret: string;
    jwt_expires_at: string;
    user: PublicUser;
  }

  function onCodeInput(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    code = maskInviteCode(input.value);
  }

  async function handleSubmit(event: SubmitEvent) {
    event.preventDefault();
    error = null;
    loading = true;

    try {
      const result = await apiFetch<RedeemResponse>(
        '/api/v1/auth/redeem-invite',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            invite_code: code,
            display_name: displayName,
          }),
        },
      );

      if (result.ok) {
        await authStore.setSession(
          result.data.jwt,
          result.data.device_secret,
          result.data.user,
        );
        window.dispatchEvent(
          new CustomEvent('auth:navigate', { detail: { path: '/today' } }),
        );
      } else {
        error = _errorMessage(result.status);
      }
    } finally {
      loading = false;
    }
  }

  function _errorMessage(status: number): string {
    switch (status) {
      case 404:
        return 'Ese código no anda. Pedile uno nuevo al organizador.';
      case 409:
        return 'Probá con otro nombre.';
      case 422:
        return 'Revisá el nombre o el código.';
      case 429:
        return 'Demasiados intentos. Probá en una hora.';
      default:
        return 'Algo salió mal. Intentá de nuevo.';
    }
  }
</script>

<main class="onboarding">
  <h1>AI Plot Twist</h1>
  <p class="subtitle">Ingresá con tu invitación para unirte a la comunidad.</p>

  <form onsubmit={handleSubmit} novalidate>
    <label for="invite-code">Código de invitación</label>
    <input
      id="invite-code"
      type="text"
      placeholder="XXXX-XXXX"
      value={code}
      oninput={onCodeInput}
      maxlength={9}
      autocomplete="off"
      autocapitalize="characters"
      spellcheck={false}
      required
      aria-describedby={error ? 'form-error' : undefined}
    />

    <label for="display-name">Tu nombre en el juego</label>
    <input
      id="display-name"
      type="text"
      placeholder="Máx. 24 caracteres"
      bind:value={displayName}
      maxlength={24}
      minlength={2}
      required
    />

    {#if error}
      <p id="form-error" class="error" role="alert">{error}</p>
    {/if}

    <button type="submit" disabled={loading || code.length < 9 || displayName.length < 2}>
      {loading ? 'Ingresando…' : 'Ingresar'}
    </button>
  </form>
</main>

<style>
  .onboarding {
    max-width: 400px;
    margin: 4rem auto;
    padding: 2rem;
    text-align: center;
  }

  form {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    margin-top: 2rem;
    text-align: left;
  }

  input {
    padding: 0.5rem 0.75rem;
    border: 1px solid #ccc;
    border-radius: 4px;
    font-size: 1rem;
  }

  button {
    padding: 0.75rem;
    background: #1a1a2e;
    color: white;
    border: none;
    border-radius: 4px;
    font-size: 1rem;
    cursor: pointer;
    margin-top: 0.5rem;
  }

  button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .error {
    color: #c0392b;
    font-size: 0.9rem;
    margin: 0;
  }

  .subtitle {
    color: #666;
  }
</style>
