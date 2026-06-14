<script lang="ts">
  /**
   * Generic skeleton placeholder.
   *
   * Module 010 / Task T-009.
   *
   * Animated shimmer block used while a store is loading. Respects
   * ``prefers-reduced-motion`` (set via theme-tokens.css media query) —
   * the keyframes become a no-op when ``--motion-fast`` is 0.
   *
   * Props:
   *   - width:   CSS length, default 100%.
   *   - height:  CSS length, default 1em.
   *   - radius:  CSS length, default var(--radius-sm).
   *   - block:   when true, renders as a div (default). When false, an
   *              inline-block span — useful inside lines of text.
   */

  interface Props {
    width?: string;
    height?: string;
    radius?: string;
    block?: boolean;
  }

  const {
    width = '100%',
    height = '1em',
    radius = 'var(--radius-sm)',
    block = true,
  }: Props = $props();
</script>

{#if block}
  <div
    class="skeleton"
    style:width
    style:height
    style:border-radius={radius}
    aria-hidden="true"
  ></div>
{:else}
  <span
    class="skeleton inline"
    style:width
    style:height
    style:border-radius={radius}
    aria-hidden="true"
  ></span>
{/if}

<style>
  .skeleton {
    background: linear-gradient(
      90deg,
      var(--color-surface-elevated) 0%,
      var(--color-border) 50%,
      var(--color-surface-elevated) 100%
    );
    background-size: 200% 100%;
    animation: shimmer 1.4s linear infinite;
  }

  .inline {
    display: inline-block;
    vertical-align: middle;
  }

  @keyframes shimmer {
    0% {
      background-position: 200% 0;
    }
    100% {
      background-position: -200% 0;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .skeleton {
      animation: none;
      background: var(--color-surface-elevated);
    }
  }
</style>
