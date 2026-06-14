<script lang="ts">
  /**
   * /me — user's own twists.
   *
   * Module 010 / Task T-003 (placeholder).
   *
   * Real screen is built out alongside T-007 / 011 work. For now this
   * just renders the existing :class:`MyTwistsPanel` so the bottom-nav
   * tab is functional.
   */
  import MyTwistsPanel from '../lib/components/MyTwistsPanel.svelte';
  import { chapterStore } from '../lib/chapter-store.svelte';
  import { S } from '../lib/strings';

  // Mirrors today.svelte: deletes only land while the submission window
  // is still open (FILTERING+ already locked them).
  const canDelete = $derived(
    chapterStore.data?.cycle_state === 'ESTRENO' ||
      chapterStore.data?.cycle_state === 'RECEPCION_IDEAS',
  );
</script>

<section class="screen">
  <h1 class="title">{S.me.title}</h1>
  <MyTwistsPanel {canDelete} />
</section>

<style>
  .screen {
    padding: var(--space-4);
  }

  .title {
    font-size: var(--font-size-xl);
    font-weight: 600;
    margin: 0 0 var(--space-4);
    color: var(--color-text);
  }
</style>
