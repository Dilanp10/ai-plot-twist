<script lang="ts">
  /**
   * One card in the vote-feed.
   *
   * Module 007 / Task T-011.
   *
   * Props:
   *   - item: the FeedItem (id / content / vote_count / has_my_vote).
   *   - disabled: parent locks the button while loading or when quota is
   *     exhausted (the card itself does not know the global quota).
   *
   * The vote action is delegated to the parent via the `onvote` callback
   * so a single voteStore.cast() call orchestrates rollback + toast.
   */
  import type { FeedItem } from '../vote-api';

  interface Props {
    item: FeedItem;
    disabled?: boolean;
    onvote: (twistId: string) => void | Promise<void>;
  }

  const props: Props = $props();

  let busy = $state(false);

  async function handleVote(): Promise<void> {
    if (props.disabled || props.item.has_my_vote || busy) return;
    busy = true;
    try {
      await props.onvote(props.item.id);
    } finally {
      busy = false;
    }
  }
</script>

<article class="card" class:voted={props.item.has_my_vote}>
  <p class="content">{props.item.content}</p>
  <div class="row">
    <span class="count" aria-label="{props.item.vote_count} votos">
      👍 {props.item.vote_count}
    </span>
    <button
      type="button"
      class="vote"
      onclick={handleVote}
      disabled={props.disabled || props.item.has_my_vote || busy}
      aria-pressed={props.item.has_my_vote}
    >
      {#if props.item.has_my_vote}
        Ya votaste
      {:else if busy}
        Votando...
      {:else}
        Votar
      {/if}
    </button>
  </div>
</article>

<style>
  .card {
    border: 1px solid #ddd;
    border-radius: 0.5rem;
    background: white;
    padding: 0.75rem 1rem;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .card.voted {
    border-color: #4caf50;
    background: #f3fbf3;
  }
  .content {
    margin: 0;
    font-size: 0.95rem;
    line-height: 1.4;
  }
  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
  }
  .count {
    font-size: 0.85rem;
    color: #555;
  }
  .vote {
    padding: 0.4rem 1rem;
    border: none;
    border-radius: 0.25rem;
    background: #1976d2;
    color: white;
    font-size: 0.9rem;
    cursor: pointer;
  }
  .vote:disabled {
    background: #ccc;
    cursor: not-allowed;
  }
  .voted .vote {
    background: #4caf50;
  }
</style>
