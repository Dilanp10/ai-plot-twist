<script lang="ts">
  /**
   * Horizontal scroll-snap carousel for picking a character before submitting
   * a twist.
   *
   * Module 013 / Delta 010 — Task T-017.
   *
   * Props:
   *   - characters:  List of available characters from the catalog.
   *   - selectedId:  Currently selected character id (null = none).
   *   - loading:     True while catalog is being fetched.
   *   - onSelect:    Callback fired when a card is clicked/focused+Enter.
   *
   * Accessibility: renders a radiogroup role with one radio per card so
   * keyboard navigation (arrow keys) and screen readers work correctly.
   */
  import type { Character } from '../character-api';

  interface Props {
    characters: Character[];
    selectedId: number | null;
    loading: boolean;
    onSelect: (id: number) => void;
  }

  const { characters, selectedId, loading, onSelect }: Props = $props();

  function handleKeydown(event: KeyboardEvent, id: number): void {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      onSelect(id);
    }
  }

  const SKELETON_COUNT = 5;
</script>

<div class="picker-section">
  <p class="picker-label">¿Quién protagoniza tu idea?</p>

  {#if loading}
    <div class="scroller" aria-busy="true" aria-label="Cargando personajes...">
      {#each Array(SKELETON_COUNT) as _, i (i)}
        <div class="card skeleton" aria-hidden="true">
          <div class="skeleton-img"></div>
          <div class="skeleton-name"></div>
        </div>
      {/each}
    </div>
  {:else if characters.length === 0}
    <p class="empty">No hay personajes disponibles.</p>
  {:else}
    <div
      class="scroller"
      role="radiogroup"
      aria-label="Elegí un personaje"
    >
      {#each characters as char (char.id)}
        <!-- svelte-ignore a11y_no_noninteractive_element_to_interactive_role -->
        <!-- svelte-ignore a11y_interactive_supports_focus -->
        <div
          class="card"
          class:selected={selectedId === char.id}
          role="radio"
          aria-checked={selectedId === char.id}
          aria-label={char.display_name}
          tabindex="0"
          onclick={() => onSelect(char.id)}
          onkeydown={(e) => handleKeydown(e, char.id)}
        >
          <img
            src={char.photo_url}
            alt={char.display_name}
            class="char-img"
            onerror={(e) => {
              const img = e.currentTarget as HTMLImageElement;
              img.src = `https://placehold.co/96x96?text=${encodeURIComponent(char.slug)}`;
            }}
          />
          <span class="char-name">{char.display_name}</span>
          {#if selectedId === char.id}
            <span class="check" aria-hidden="true">✓</span>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .picker-section {
    margin-bottom: 1rem;
  }
  .picker-label {
    font-size: 0.85rem;
    color: #444;
    margin: 0 0 0.5rem 0;
  }
  .scroller {
    display: flex;
    gap: 0.5rem;
    overflow-x: auto;
    scroll-snap-type: x mandatory;
    padding-bottom: 0.25rem;
    -webkit-overflow-scrolling: touch;
  }
  .scroller::-webkit-scrollbar {
    height: 4px;
  }
  .scroller::-webkit-scrollbar-thumb {
    background: #ccc;
    border-radius: 2px;
  }
  .card {
    flex: 0 0 auto;
    scroll-snap-align: start;
    width: 80px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.25rem;
    padding: 0.4rem;
    border: 2px solid transparent;
    border-radius: 0.5rem;
    cursor: pointer;
    background: #f8f8f8;
    position: relative;
    transition: border-color 0.15s, box-shadow 0.15s;
  }
  .card:hover {
    border-color: #999;
  }
  .card.selected {
    border-color: #1a1a2e;
    box-shadow: 0 0 0 1px #1a1a2e;
    background: #eeeef5;
  }
  .card:focus-visible {
    outline: 2px solid #1a1a2e;
    outline-offset: 2px;
  }
  .char-img {
    width: 56px;
    height: 56px;
    border-radius: 50%;
    object-fit: cover;
    display: block;
  }
  .char-name {
    font-size: 0.7rem;
    text-align: center;
    color: #333;
    line-height: 1.2;
    word-break: break-word;
    max-width: 72px;
  }
  .check {
    position: absolute;
    top: 2px;
    right: 4px;
    font-size: 0.75rem;
    color: #1a1a2e;
    font-weight: 700;
  }

  /* Skeleton */
  .skeleton {
    cursor: default;
    animation: shimmer 1.2s ease-in-out infinite;
    background: #e8e8e8;
  }
  .skeleton-img {
    width: 56px;
    height: 56px;
    border-radius: 50%;
    background: #d0d0d0;
  }
  .skeleton-name {
    width: 52px;
    height: 10px;
    border-radius: 4px;
    background: #d0d0d0;
  }
  @keyframes shimmer {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
  }

  .empty {
    font-size: 0.85rem;
    color: #888;
    margin: 0;
  }
</style>
