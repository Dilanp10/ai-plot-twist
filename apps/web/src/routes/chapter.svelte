<script lang="ts">
  /**
   * /chapter/:id — chapter detail view (read-only, no CTAs).
   *
   * Opened by tapping a card in the series album (/series).
   * Fetches the chapter by public_id and renders panels + narration +
   * cliffhanger. No idea submission or voting here.
   */
  import { onMount } from 'svelte';
  import { apiFetch } from '../lib/api';
  import { router } from '../lib/router.svelte';
  import Skeleton from '../lib/components/Skeleton.svelte';

  // ---------------------------------------------------------------------------
  // Props — the chapter id comes from the router path segment
  // ---------------------------------------------------------------------------

  interface Props {
    chapterId: string;
  }

  let { chapterId }: Props = $props();

  // ---------------------------------------------------------------------------
  // Types (mirrors contracts/chapters.yaml#ChapterResponseDTO)
  // ---------------------------------------------------------------------------

  interface Panel {
    idx: number;
    image_url: string;
    tts_url?: string | null;
    narration: string;
    mood: string;
  }

  interface ChapterDetail {
    id: string;
    day_index: number;
    title: string;
    synopsis: string;
    released_at: string;
    panels: Panel[];
    cliffhanger: string;
  }

  interface ChapterResponse {
    season: { slug: string; title: string };
    chapter: ChapterDetail;
  }

  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------

  type Status = 'loading' | 'ok' | 'error';

  let status = $state<Status>('loading');
  let data = $state<ChapterResponse | null>(null);
  let playingIdx = $state<number | null>(null);

  // ---------------------------------------------------------------------------
  // Fetch
  // ---------------------------------------------------------------------------

  async function load(): Promise<void> {
    status = 'loading';
    const result = await apiFetch<ChapterResponse>(`/api/v1/chapters/${chapterId}`);
    if (result.ok && result.data) {
      data = result.data;
      status = 'ok';
    } else {
      status = 'error';
    }
  }

  onMount(() => {
    void load();
  });

  // ---------------------------------------------------------------------------
  // Audio
  // ---------------------------------------------------------------------------

  let audioEl: HTMLAudioElement | undefined;

  function toggleAudio(panel: Panel): void {
    if (!panel.tts_url) return;
    if (playingIdx === panel.idx) {
      audioEl?.pause();
      playingIdx = null;
      return;
    }
    audioEl?.pause();
    playingIdx = panel.idx;
    audioEl = new Audio(panel.tts_url);
    audioEl.play().catch(() => {});
    audioEl.onended = () => { playingIdx = null; };
  }
</script>

<main class="chapter-detail">
  <!-- Back button -->
  <button class="back-btn" type="button" onclick={() => router.navigate('/series')}>
    ‹ Serie
  </button>

  {#if status === 'loading'}
    <div class="skeleton-wrap">
      <Skeleton height="1.5rem" width="70%" />
      <Skeleton height="1rem" width="40%" />
      <Skeleton height="280px" radius="var(--radius-md)" />
      <Skeleton height="1rem" />
      <Skeleton height="1rem" width="80%" />
    </div>

  {:else if status === 'ok' && data}
    {@const ch = data.chapter}

    <header class="meta">
      <p class="season-label">{data.season.title} · Cap. {ch.day_index}</p>
      <h1 class="chapter-title">{ch.title}</h1>
      <p class="synopsis">{ch.synopsis}</p>
    </header>

    <section class="panels">
      {#each ch.panels as panel (panel.idx)}
        <figure class="panel">
          {#if panel.image_url}
            <img src={panel.image_url} alt={panel.narration} loading="lazy" />
          {:else}
            <div class="panel-placeholder" aria-hidden="true"></div>
          {/if}
          <figcaption>
            <p class="narration">{panel.narration}</p>
            {#if panel.tts_url}
              <button
                class="audio-btn"
                class:playing={playingIdx === panel.idx}
                type="button"
                onclick={() => toggleAudio(panel)}
                aria-label={playingIdx === panel.idx ? 'Pausar narración' : 'Escuchar narración'}
              >
                {playingIdx === panel.idx ? '⏸' : '▶'} Narración
              </button>
            {/if}
          </figcaption>
        </figure>
      {/each}
    </section>

    <p class="cliffhanger">"{ch.cliffhanger}"</p>

  {:else}
    <div class="banner error">
      <p>No pudimos cargar este capítulo.</p>
      <button type="button" onclick={() => void load()}>Reintentar</button>
    </div>
  {/if}
</main>

<style>
  .chapter-detail {
    max-width: 720px;
    margin: 0 auto;
    padding-bottom: 3rem;
    color: var(--color-text);
  }

  /* ── Back ─────────────────────────────────────────────────────────────── */

  .back-btn {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    padding: var(--space-3) var(--space-4);
    background: transparent;
    border: none;
    color: var(--color-accent);
    font-size: var(--font-size-sm);
    font-weight: 600;
    cursor: pointer;
    letter-spacing: 0.02em;
  }

  /* ── Meta ─────────────────────────────────────────────────────────────── */

  .meta {
    padding: var(--space-2) var(--space-4) var(--space-5);
    text-align: center;
  }

  .season-label {
    font-size: var(--font-size-xs);
    color: var(--color-accent);
    text-transform: uppercase;
    letter-spacing: 0.1em;
    font-weight: 600;
    margin: 0 0 var(--space-2);
  }

  .chapter-title {
    font-size: 1.6rem;
    font-weight: 800;
    margin: 0 0 var(--space-3);
    line-height: 1.2;
  }

  .synopsis {
    font-size: var(--font-size-sm);
    color: var(--color-text-muted);
    line-height: 1.6;
    margin: 0;
  }

  /* ── Panels ───────────────────────────────────────────────────────────── */

  .panels {
    display: flex;
    flex-direction: column;
    gap: var(--space-6);
    padding: 0 var(--space-4);
    margin-bottom: var(--space-6);
  }

  .panel {
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }

  .panel img {
    width: 100%;
    aspect-ratio: 16 / 9;
    object-fit: cover;
    border-radius: var(--radius-md);
    background: var(--color-surface);
  }

  .panel-placeholder {
    width: 100%;
    aspect-ratio: 16 / 9;
    border-radius: var(--radius-md);
    background: var(--color-surface);
  }

  figcaption {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }

  .narration {
    font-size: var(--font-size-base);
    line-height: 1.6;
    margin: 0;
    color: var(--color-text);
  }

  .audio-btn {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    padding: var(--space-1) var(--space-3);
    background: rgba(255, 255, 255, 0.06);
    border: 1px solid var(--color-border);
    border-radius: 999px;
    color: var(--color-text-muted);
    font-size: var(--font-size-xs);
    font-weight: 600;
    cursor: pointer;
    transition: all var(--motion-fast) var(--motion-easing);
    width: fit-content;
  }

  .audio-btn.playing {
    background: rgba(255, 46, 99, 0.15);
    border-color: var(--color-accent);
    color: var(--color-accent);
  }

  /* ── Cliffhanger ──────────────────────────────────────────────────────── */

  .cliffhanger {
    font-style: italic;
    text-align: center;
    padding: var(--space-5) var(--space-6);
    font-size: 1.1rem;
    line-height: 1.5;
    color: var(--color-text);
    border-top: 1px solid var(--color-border);
  }

  /* ── Skeleton ─────────────────────────────────────────────────────────── */

  .skeleton-wrap {
    padding: var(--space-4);
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }

  /* ── Error ────────────────────────────────────────────────────────────── */

  .banner.error {
    text-align: center;
    padding: var(--space-8) var(--space-4);
    color: var(--color-text-muted);
  }

  .banner button {
    margin-top: var(--space-3);
    padding: var(--space-2) var(--space-5);
    background: var(--color-accent);
    color: #fff;
    border: none;
    border-radius: var(--radius-sm);
    cursor: pointer;
    font-weight: 600;
  }
</style>
