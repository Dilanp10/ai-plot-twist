<script lang="ts">
  /**
   * Modal for submitting a new twist.
   *
   * Module 005 / Task T-013.
   * Updated: Delta 010 — adds CharacterPicker and character_id to submission.
   *
   * Props:
   *   - open:      Whether the modal is visible.
   *   - chapterId: The current live chapter's public_id.
   *   - onClose:   Callback fired after submit success OR cancel.
   *
   * Flow:
   *   1. On open, triggers ensureCatalogLoaded() (no-op if already loaded).
   *   2. User picks a character in CharacterPicker.
   *   3. User types their idea (≥5 chars, ≤280).
   *   4. Submit is enabled only when both conditions are met.
   *   5. On success, twistStore clears selectedCharacterId; modal closes.
   */
  import { twistStore } from '../twist-store.svelte';
  import CharacterPicker from './CharacterPicker.svelte';

  interface Props {
    open: boolean;
    chapterId: string;
    onClose: () => void;
  }

  const props: Props = $props();

  const MAX_LEN = 280;
  const MIN_LEN = 5;

  let content = $state('');
  let submitting = $state(false);
  let localError = $state<string | null>(null);

  const remaining = $derived(MAX_LEN - content.length);
  const tooShort = $derived(content.trim().length < MIN_LEN);
  const overLimit = $derived(content.length > MAX_LEN);
  const canSubmit = $derived(
    !submitting && !tooShort && !overLimit && twistStore.selectedCharacterId !== null,
  );

  $effect(() => {
    if (props.open) void twistStore.ensureCatalogLoaded();
  });

  async function handleSubmit(): Promise<void> {
    if (!canSubmit || twistStore.selectedCharacterId === null) return;
    submitting = true;
    localError = null;
    const ok = await twistStore.submit(props.chapterId, content, twistStore.selectedCharacterId);
    submitting = false;
    if (ok) {
      content = '';
      props.onClose();
    } else {
      localError = twistStore.errorMessage ?? 'No se pudo enviar.';
    }
  }

  function handleCancel(): void {
    if (submitting) return;
    content = '';
    localError = null;
    props.onClose();
  }
</script>

{#if props.open}
  <div
    class="backdrop"
    role="dialog"
    aria-modal="true"
    aria-labelledby="twist-modal-title"
  >
    <div class="modal">
      <h2 id="twist-modal-title">Tirá una idea</h2>
      <p class="subtitle">
        Elegí un personaje y escribí cómo seguiría la historia.
      </p>

      <CharacterPicker
        characters={twistStore.catalog}
        selectedId={twistStore.selectedCharacterId}
        loading={!twistStore.catalogLoaded}
        onSelect={(id) => twistStore.selectCharacter(id)}
      />

      <label class="textarea-label" for="twist-content">Tu idea</label>
      <textarea
        id="twist-content"
        bind:value={content}
        maxlength={MAX_LEN}
        rows={5}
        placeholder="Escribí acá tu giro..."
        disabled={submitting}
        aria-describedby="twist-counter twist-error"
      ></textarea>

      <div id="twist-counter" class="counter" class:over={overLimit}>
        {content.length} / {MAX_LEN} · quedan {remaining}
      </div>

      {#if localError}
        <div id="twist-error" class="error-pill" role="alert">
          {localError}
        </div>
      {/if}

      {#if twistStore.selectedCharacterId === null && twistStore.catalogLoaded && twistStore.catalog.length > 0}
        <p class="hint" role="status">Elegí un personaje para poder enviar.</p>
      {/if}

      <div class="actions">
        <button type="button" class="cancel" onclick={handleCancel} disabled={submitting}>
          Cancelar
        </button>
        <button
          type="button"
          class="submit"
          onclick={handleSubmit}
          disabled={!canSubmit}
        >
          {submitting ? 'Enviando...' : 'Tirá la idea'}
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
  }
  .modal {
    background: white;
    border-radius: 0.75rem;
    padding: 1.5rem;
    max-width: 28rem;
    width: 90%;
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.2);
    max-height: 90vh;
    overflow-y: auto;
  }
  h2 {
    margin: 0 0 0.5rem 0;
    font-size: 1.25rem;
  }
  .subtitle {
    margin: 0 0 1rem 0;
    color: #666;
    font-size: 0.9rem;
  }
  .textarea-label {
    display: block;
    font-size: 0.85rem;
    margin-bottom: 0.25rem;
    color: #444;
  }
  textarea {
    width: 100%;
    padding: 0.5rem;
    border: 1px solid #ccc;
    border-radius: 0.375rem;
    font-family: inherit;
    font-size: 1rem;
    resize: vertical;
    box-sizing: border-box;
  }
  textarea:disabled {
    background: #f5f5f5;
  }
  .counter {
    text-align: right;
    font-size: 0.85rem;
    color: #888;
    margin-top: 0.25rem;
  }
  .counter.over {
    color: #d33;
    font-weight: 600;
  }
  .error-pill {
    background: #ffe6e6;
    color: #a00;
    padding: 0.5rem 0.75rem;
    border-radius: 0.375rem;
    margin-top: 0.75rem;
    font-size: 0.9rem;
  }
  .hint {
    font-size: 0.8rem;
    color: #888;
    margin: 0.5rem 0 0 0;
  }
  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
    margin-top: 1rem;
  }
  button {
    padding: 0.5rem 1rem;
    border: none;
    border-radius: 0.375rem;
    font-size: 1rem;
    cursor: pointer;
  }
  .cancel {
    background: #eee;
    color: #333;
  }
  .submit {
    background: #1a1a2e;
    color: white;
  }
  .submit:disabled,
  .cancel:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
</style>
