<script lang="ts">
  /**
   * /admin — panel de administración para subir el video del día.
   *
   * Module 014 / Task T-006.
   *
   * Flujo:
   *   1. Si no hay admin JWT en sessionStorage → pantalla de login.
   *   2. Tras login → carga el ciclo activo (GET /admin/cycle).
   *   3. Admin elige un .mp4, hace click en "Subir video":
   *      a. POST /admin/chapters/{id}/video-upload-url → URL presignada
   *      b. PUT directo a R2 con progress bar (via XHR)
   *      c. PUT /admin/chapters/{id}/video → confirma la URL pública
   *   4. Éxito → muestra mensaje de confirmación.
   */
  import { onMount } from 'svelte';
  import {
    adminConfirmVideo,
    adminGetCycle,
    adminGetUploadUrl,
    adminLogin,
    clearAdminToken,
    getAdminToken,
    setAdminToken,
    uploadToR2,
    type AdminCycleResponse,
  } from '../lib/admin-api';

  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------

  type View = 'login' | 'panel' | 'success';

  let view = $state<View>('login');
  let busy = $state(false);
  let error = $state<string | null>(null);

  // Login form
  let password = $state('');

  // Panel
  let cycle = $state<AdminCycleResponse | null>(null);
  let selectedFile = $state<File | null>(null);
  let uploadPct = $state(0);

  // ---------------------------------------------------------------------------
  // Boot: if token already in sessionStorage, skip login
  // ---------------------------------------------------------------------------

  onMount(() => {
    if (getAdminToken()) {
      void loadCycle();
    }
  });

  // ---------------------------------------------------------------------------
  // Actions
  // ---------------------------------------------------------------------------

  async function handleLogin(e: SubmitEvent): Promise<void> {
    e.preventDefault();
    error = null;
    busy = true;
    try {
      const result = await adminLogin(password);
      if (!result.ok) {
        error = result.message;
        return;
      }
      setAdminToken(result.data.token);
      await loadCycle();
    } finally {
      busy = false;
    }
  }

  async function loadCycle(): Promise<void> {
    error = null;
    busy = true;
    try {
      const result = await adminGetCycle();
      if (!result.ok) {
        if (result.status === 401 || result.status === 403) {
          clearAdminToken();
          view = 'login';
          return;
        }
        error = result.message;
        return;
      }
      cycle = result.data;
      view = 'panel';
    } finally {
      busy = false;
    }
  }

  function handleFileChange(e: Event): void {
    const input = e.target as HTMLInputElement;
    selectedFile = input.files?.[0] ?? null;
    error = null;
  }

  async function handleUpload(): Promise<void> {
    if (!selectedFile || !cycle) return;
    error = null;
    busy = true;
    uploadPct = 0;

    try {
      // 1. Get presigned URL
      const urlResult = await adminGetUploadUrl(cycle.chapter_id);
      if (!urlResult.ok) {
        error = urlResult.message;
        return;
      }
      const { upload_url, public_url } = urlResult.data;

      // 2. Upload to R2
      await uploadToR2(upload_url, selectedFile, (pct) => {
        uploadPct = pct;
      });

      // 3. Confirm
      const confirmResult = await adminConfirmVideo(cycle.chapter_id, public_url);
      if (!confirmResult.ok) {
        error = confirmResult.message;
        return;
      }

      view = 'success';
    } finally {
      busy = false;
    }
  }

  function handleSignOut(): void {
    clearAdminToken();
    cycle = null;
    password = '';
    selectedFile = null;
    uploadPct = 0;
    error = null;
    view = 'login';
  }

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  const STATE_LABELS: Record<string, string> = {
    GENERACION: 'Generación (listo para subir video)',
    VOTACION: 'Votación en curso',
    RECEPCION_IDEAS: 'Recepción de ideas',
    ESTRENO: 'Estreno',
    PENDING_RELEASE: 'Pendiente de estreno',
    FILTERING: 'Filtrando ideas',
    FAILED: 'Ciclo fallido',
  };
</script>

<div class="admin-shell">
  <!-- ── Login ── -->
  {#if view === 'login'}
    <div class="card">
      <h1 class="title">Panel Admin</h1>
      <p class="subtitle">AI Plot Twist</p>

      <form onsubmit={handleLogin} class="form">
        <label class="field">
          <span class="field-label">Contraseña</span>
          <input
            type="password"
            class="field-input"
            bind:value={password}
            placeholder="••••••"
            disabled={busy}
            autocomplete="current-password"
            required
          />
        </label>

        {#if error}
          <p class="error-msg" role="alert">{error}</p>
        {/if}

        <button type="submit" class="btn-primary" disabled={busy || !password}>
          {busy ? 'Entrando…' : 'Entrar'}
        </button>
      </form>
    </div>

  <!-- ── Panel ── -->
  {:else if view === 'panel' && cycle}
    <div class="panel">
      <header class="panel-header">
        <div>
          <h1 class="title">Panel Admin</h1>
          <p class="state-badge" class:generacion={cycle.cycle_state === 'GENERACION'}>
            {STATE_LABELS[cycle.cycle_state] ?? cycle.cycle_state}
          </p>
        </div>
        <button type="button" class="btn-ghost" onclick={handleSignOut}>Salir</button>
      </header>

      <!-- Cycle info -->
      <section class="section">
        <h2 class="section-title">Ciclo del {cycle.cycle_date}</h2>
        <p class="meta">Capítulo #{cycle.chapter_id}</p>
      </section>

      <!-- Winner -->
      {#if cycle.winner}
        <section class="section winner">
          <h2 class="section-title">🏆 Idea ganadora</h2>
          <blockquote class="twist-text">"{cycle.winner.twist_text}"</blockquote>
          <div class="winner-meta">
            <span class="meta">por <strong>{cycle.winner.author_display_name}</strong></span>
            <span class="vote-chip">{cycle.winner.vote_count} votos</span>
          </div>
          <div class="character-row">
            <img
              src={cycle.winner.character_photo_url}
              alt={cycle.winner.character_name}
              class="character-photo"
            />
            <div>
              <p class="character-name">{cycle.winner.character_name}</p>
              <p class="meta">{cycle.winner.character_slug}</p>
            </div>
          </div>
        </section>
      {:else}
        <section class="section">
          <p class="meta">Sin idea ganadora aún.</p>
        </section>
      {/if}

      <!-- Upload -->
      <section class="section">
        <h2 class="section-title">Subir video</h2>

        {#if cycle.cycle_state !== 'GENERACION'}
          <p class="warning">
            El ciclo está en <strong>{cycle.cycle_state}</strong>. Solo se puede subir el
            video durante la fase de Generación.
          </p>
        {:else}
          <label class="file-label">
            <input
              type="file"
              accept="video/mp4"
              onchange={handleFileChange}
              disabled={busy}
              class="file-input"
            />
            <span class="file-btn">{selectedFile ? selectedFile.name : 'Elegir archivo .mp4'}</span>
          </label>

          {#if busy && uploadPct > 0}
            <div class="progress-bar" role="progressbar" aria-valuenow={uploadPct} aria-valuemax={100}>
              <div class="progress-fill" style="width:{uploadPct}%"></div>
              <span class="progress-label">{uploadPct}%</span>
            </div>
          {/if}

          {#if error}
            <p class="error-msg" role="alert">{error}</p>
          {/if}

          <button
            type="button"
            class="btn-primary"
            onclick={handleUpload}
            disabled={busy || !selectedFile}
          >
            {busy ? (uploadPct > 0 ? `Subiendo ${uploadPct}%…` : 'Preparando…') : 'Subir video'}
          </button>
        {/if}
      </section>

      <button type="button" class="btn-ghost refresh" onclick={loadCycle} disabled={busy}>
        Actualizar estado
      </button>
    </div>

  <!-- ── Success ── -->
  {:else if view === 'success'}
    <div class="card">
      <p class="success-icon">✅</p>
      <h1 class="title">¡Video subido!</h1>
      <p class="subtitle">El video ya está disponible en el capítulo.</p>
      <button type="button" class="btn-primary" onclick={loadCycle}>Volver al panel</button>
    </div>
  {/if}
</div>

<style>
  .admin-shell {
    min-height: 100dvh;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: var(--space-5) var(--space-4);
    background: var(--color-bg);
  }

  /* ── Card (login / success) ── */
  .card {
    width: 100%;
    max-width: 400px;
    margin-top: var(--space-8);
    padding: var(--space-6);
    background: var(--color-surface);
    border-radius: var(--radius-lg);
    border: 1px solid var(--color-border);
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
  }

  /* ── Panel (full width, max 540px) ── */
  .panel {
    width: 100%;
    max-width: 540px;
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
  }

  .panel-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    padding-top: var(--space-2);
  }

  /* ── Typography ── */
  .title {
    font-size: var(--font-size-xl);
    font-weight: 700;
    color: var(--color-text);
    margin: 0;
  }

  .subtitle {
    color: var(--color-text-muted);
    font-size: var(--font-size-sm);
    margin: 0;
  }

  .section-title {
    font-size: var(--font-size-md);
    font-weight: 600;
    color: var(--color-text);
    margin: 0 0 var(--space-2);
  }

  .meta {
    color: var(--color-text-muted);
    font-size: var(--font-size-sm);
    margin: 0;
  }

  /* ── State badge ── */
  .state-badge {
    display: inline-block;
    margin-top: var(--space-1);
    padding: 2px var(--space-2);
    border-radius: var(--radius-sm);
    font-size: var(--font-size-xs);
    background: var(--color-surface-elevated);
    color: var(--color-text-muted);
    border: 1px solid var(--color-border);
  }

  .state-badge.generacion {
    background: #dcfce7;
    color: #15803d;
    border-color: #86efac;
  }

  /* ── Section ── */
  .section {
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
    padding: var(--space-4);
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }

  /* ── Winner ── */
  .twist-text {
    font-size: var(--font-size-md);
    color: var(--color-text);
    font-style: italic;
    border-left: 3px solid var(--color-danger);
    padding-left: var(--space-3);
    margin: 0;
  }

  .winner-meta {
    display: flex;
    align-items: center;
    gap: var(--space-3);
  }

  .vote-chip {
    background: var(--color-surface-elevated);
    color: var(--color-text);
    border: 1px solid var(--color-border);
    border-radius: 999px;
    padding: 2px var(--space-2);
    font-size: var(--font-size-xs);
    font-weight: 600;
  }

  .character-row {
    display: flex;
    gap: var(--space-3);
    align-items: center;
  }

  .character-photo {
    width: 56px;
    height: 56px;
    border-radius: var(--radius-md);
    object-fit: cover;
    border: 1px solid var(--color-border);
  }

  .character-name {
    font-weight: 600;
    color: var(--color-text);
    margin: 0;
    font-size: var(--font-size-sm);
  }

  /* ── Form ── */
  .form {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }

  .field-label {
    font-size: var(--font-size-sm);
    color: var(--color-text-muted);
  }

  .field-input {
    padding: var(--space-3);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-surface);
    color: var(--color-text);
    font: inherit;
    font-size: var(--font-size-md);
  }

  .field-input:focus {
    outline: 2px solid var(--color-danger);
    outline-offset: 2px;
  }

  /* ── File input ── */
  .file-label {
    display: block;
    cursor: pointer;
  }

  .file-input {
    position: absolute;
    width: 1px;
    height: 1px;
    opacity: 0;
    overflow: hidden;
  }

  .file-btn {
    display: block;
    padding: var(--space-3);
    border: 1px dashed var(--color-border);
    border-radius: var(--radius-md);
    color: var(--color-text-muted);
    font-size: var(--font-size-sm);
    text-align: center;
    transition: background 0.15s;
  }

  .file-label:hover .file-btn {
    background: var(--color-surface-elevated);
  }

  /* ── Progress ── */
  .progress-bar {
    position: relative;
    height: 24px;
    background: var(--color-surface-elevated);
    border-radius: var(--radius-sm);
    overflow: hidden;
    border: 1px solid var(--color-border);
  }

  .progress-fill {
    position: absolute;
    top: 0;
    left: 0;
    height: 100%;
    background: var(--color-danger);
    transition: width 0.2s;
  }

  .progress-label {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: var(--font-size-xs);
    font-weight: 600;
    color: var(--color-text);
    mix-blend-mode: difference;
  }

  /* ── Buttons ── */
  .btn-primary {
    width: 100%;
    padding: var(--space-3) var(--space-4);
    background: var(--color-danger);
    color: var(--color-text-inverse);
    border: 0;
    border-radius: var(--radius-md);
    font: inherit;
    font-weight: 600;
    font-size: var(--font-size-md);
    cursor: pointer;
    transition: opacity 0.15s;
  }

  .btn-primary:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .btn-ghost {
    background: transparent;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    padding: var(--space-2) var(--space-3);
    font: inherit;
    font-size: var(--font-size-sm);
    color: var(--color-text-muted);
    cursor: pointer;
  }

  .btn-ghost:hover:not(:disabled) {
    background: var(--color-surface-elevated);
  }

  .btn-ghost.refresh {
    align-self: center;
    width: auto;
    font-size: var(--font-size-xs);
  }

  /* ── Messages ── */
  .error-msg {
    color: var(--color-danger);
    font-size: var(--font-size-sm);
    margin: 0;
  }

  .warning {
    background: #fef9c3;
    border: 1px solid #fde047;
    border-radius: var(--radius-sm);
    padding: var(--space-3);
    font-size: var(--font-size-sm);
    color: #713f12;
    margin: 0;
  }

  .success-icon {
    font-size: 3rem;
    text-align: center;
    margin: 0;
  }
</style>
