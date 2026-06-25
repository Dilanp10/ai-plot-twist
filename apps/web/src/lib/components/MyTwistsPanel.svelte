<script lang="ts">
  /**
   * Collapsible panel listing the user's twists for the current chapter.
   *
   * Module 005 / Task T-013.
   *
   * Props:
   *   - canDelete: Whether the delete buttons are clickable.  The route
   *                passes ``cycle_state === 'RECEPCION_IDEAS' && now < submit_until``.
   *
   * Loads on mount via twistStore.load().  Status badges per twist:
   *   pending_review → "Pendiente"
   *   approved       → "Aprobada"
   *   rejected_*     → "Rechazada" (with director_reason tooltip)
   *   deleted_by_user → "Eliminada"
   */
  import { onMount } from 'svelte';
  import { twistStore } from '../twist-store.svelte';
  import type { TwistMine, TwistStatus } from '../twist-api';

  interface Props {
    canDelete: boolean;
  }

  const props: Props = $props();

  let expanded = $state(true);
  let removingId = $state<string | null>(null);

  onMount(() => {
    void twistStore.load();
  });

  const STATUS_LABELS: Record<TwistStatus, string> = {
    pending_review: 'Pendiente',
    approved: 'Aprobada',
    rejected_offensive: 'Rechazada',
    rejected_incoherent: 'Rechazada',
    rejected_spam: 'Rechazada',
    deleted_by_user: 'Eliminada',
  };

  function statusKind(s: TwistStatus): 'pending' | 'approved' | 'rejected' | 'deleted' {
    if (s === 'pending_review') return 'pending';
    if (s === 'approved') return 'approved';
    if (s === 'deleted_by_user') return 'deleted';
    return 'rejected';
  }

  function canDeleteItem(t: TwistMine): boolean {
    return props.canDelete && t.status === 'pending_review';
  }

  async function handleDelete(publicId: string): Promise<void> {
    removingId = publicId;
    await twistStore.remove(publicId);
    removingId = null;
  }
</script>

<section class="panel" aria-labelledby="my-twists-heading">
  <button
    type="button"
    class="header"
    onclick={() => (expanded = !expanded)}
    aria-expanded={expanded}
    aria-controls="my-twists-content"
  >
    <h3 id="my-twists-heading">Mis ideas</h3>
    <span class="quota">
      {twistStore.quota.used} / {twistStore.quota.max}
    </span>
    <span class="chevron" class:open={expanded} aria-hidden="true">▾</span>
  </button>

  {#if expanded}
    <div id="my-twists-content" class="content">
      {#if twistStore.status === 'loading'}
        <p class="muted">Cargando...</p>
      {:else if twistStore.status === 'error'}
        <p class="error">{twistStore.errorMessage ?? 'Error al cargar.'}</p>
      {:else if twistStore.status === 'maintenance'}
        <p class="muted">Servicio en mantenimiento.</p>
      {:else if twistStore.mine.length === 0}
        <p class="muted">Todavía no tiraste ninguna idea.</p>
      {:else}
        <ul>
          {#each twistStore.mine as twist (twist.public_id)}
            <li class="item kind-{statusKind(twist.status)}">
              {#if twist.character}
                <div class="char-row">
                  <img
                    class="char-thumb"
                    src={twist.character.photo_url}
                    alt={twist.character.display_name}
                    onerror={(e) => {
                      const img = e.currentTarget as HTMLImageElement;
                      img.src = `https://placehold.co/32x32?text=${encodeURIComponent(twist.character!.slug)}`;
                    }}
                  />
                  <span class="char-name">{twist.character.display_name}</span>
                </div>
              {/if}
              <p class="content-text">{twist.content}</p>
              <div class="row">
                <span class="badge">{STATUS_LABELS[twist.status]}</span>
                {#if twist.status.startsWith('rejected') && twist.director_reason}
                  <span class="reason" title={twist.director_reason}>
                    motivo
                  </span>
                {/if}
                {#if canDeleteItem(twist)}
                  <button
                    type="button"
                    class="delete"
                    disabled={removingId === twist.public_id}
                    onclick={() => handleDelete(twist.public_id)}
                  >
                    {removingId === twist.public_id ? 'Borrando...' : 'Borrar'}
                  </button>
                {/if}
              </div>
            </li>
          {/each}
        </ul>
        <p class="remaining">
          Te quedan {twistStore.quota.remaining} ideas.
        </p>
      {/if}
    </div>
  {/if}
</section>

<style>
  .panel {
    border: 1px solid #ddd;
    border-radius: 0.5rem;
    background: white;
  }
  .header {
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.75rem 1rem;
    background: none;
    border: none;
    cursor: pointer;
    text-align: left;
    font: inherit;
  }
  h3 {
    margin: 0;
    font-size: 1rem;
  }
  .quota {
    font-size: 0.85rem;
    color: #666;
    margin-left: auto;
    margin-right: 0.5rem;
  }
  .chevron {
    transition: transform 0.15s ease;
  }
  .chevron.open {
    transform: rotate(180deg);
  }
  .content {
    padding: 0 1rem 1rem 1rem;
  }
  .muted {
    color: #888;
    font-size: 0.9rem;
  }
  .error {
    color: #a00;
    font-size: 0.9rem;
  }
  ul {
    list-style: none;
    padding: 0;
    margin: 0;
  }
  .item {
    padding: 0.5rem 0;
    border-top: 1px solid #eee;
  }
  .char-row {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    margin-bottom: 0.25rem;
  }
  .char-thumb {
    width: 24px;
    height: 24px;
    border-radius: 50%;
    object-fit: cover;
    flex-shrink: 0;
  }
  .char-name {
    font-size: 0.78rem;
    color: #555;
  }
  .content-text {
    margin: 0 0 0.25rem 0;
    font-size: 0.95rem;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .badge {
    font-size: 0.75rem;
    padding: 0.1rem 0.5rem;
    border-radius: 1rem;
    background: #eee;
  }
  .kind-pending .badge {
    background: #fff3cd;
    color: #856404;
  }
  .kind-approved .badge {
    background: #d4edda;
    color: #155724;
  }
  .kind-rejected .badge {
    background: #f8d7da;
    color: #721c24;
  }
  .kind-deleted .badge {
    background: #e0e0e0;
    color: #555;
  }
  .reason {
    font-size: 0.75rem;
    color: #888;
    text-decoration: underline dotted;
    cursor: help;
  }
  .delete {
    margin-left: auto;
    padding: 0.25rem 0.5rem;
    background: #eee;
    color: #333;
    border: none;
    border-radius: 0.25rem;
    font-size: 0.85rem;
    cursor: pointer;
  }
  .delete:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .remaining {
    margin: 0.75rem 0 0 0;
    font-size: 0.85rem;
    color: #555;
  }
</style>
