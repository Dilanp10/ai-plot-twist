<script lang="ts">
  /**
   * Countdown component — ticks once a second.
   *
   * Module 004 / Task T-013.
   *
   * Props:
   *   - label:  String shown above the countdown ("Cierra la ronda de ideas").
   *   - target: Date the countdown is racing toward (UTC).
   *
   * The interval is cleaned up automatically when the component unmounts;
   * Svelte 5 ``$effect`` returns a teardown function which the runtime calls
   * on destroy.
   */
  import { formatRemaining } from './window-countdown';

  interface Props {
    label: string;
    target: Date;
  }

  const { label, target }: Props = $props();

  let remaining = $state(formatRemaining(target));

  $effect(() => {
    // Re-establish the timer when ``target`` changes (e.g. cycle advances).
    remaining = formatRemaining(target);
    const id = setInterval(() => {
      remaining = formatRemaining(target);
    }, 1000);
    return () => clearInterval(id);
  });
</script>

<div class="countdown" role="timer" aria-live="polite">
  <span class="label">{label}</span>
  <span class="value">{remaining}</span>
</div>

<style>
  .countdown {
    display: inline-flex;
    flex-direction: column;
    align-items: center;
    gap: 0.25rem;
  }

  .label {
    font-size: 0.85rem;
    color: #666;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .value {
    font-size: 1.5rem;
    font-weight: 700;
    color: #1a1a2e;
    font-variant-numeric: tabular-nums;
  }
</style>
