<script lang="ts">
  /**
   * Visual indicator of how many votes the user has cast out of the cap.
   *
   * Module 007 / Task T-011.
   *
   * Renders ``max`` dots, the first ``used`` filled. Reads from voteStore
   * directly so callers don't have to thread props.
   */
  import { voteStore } from '../vote-store.svelte';
</script>

<div class="indicator" aria-label="Tus votos: {voteStore.quota.used} de {voteStore.quota.max}">
  <span class="label">Mis votos</span>
  <div class="dots" role="presentation">
    {#each Array.from({ length: voteStore.quota.max }, (_, i) => i) as i (i)}
      <span class="dot" class:filled={i < voteStore.quota.used}></span>
    {/each}
  </div>
  <span class="text">{voteStore.quota.used} / {voteStore.quota.max}</span>
</div>

<style>
  .indicator {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.85rem;
  }
  .label {
    color: #555;
  }
  .dots {
    display: flex;
    gap: 0.25rem;
  }
  .dot {
    width: 0.65rem;
    height: 0.65rem;
    border-radius: 50%;
    background: #e0e0e0;
    border: 1px solid #ccc;
  }
  .dot.filled {
    background: #4caf50;
    border-color: #2e7d32;
  }
  .text {
    color: #555;
    font-variant-numeric: tabular-nums;
  }
</style>
