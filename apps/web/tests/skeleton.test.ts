/**
 * Unit tests: Skeleton placeholder.
 *
 * Module 010 / Task T-009.
 */
import { cleanup, render } from '@testing-library/svelte';
import { afterEach, describe, expect, it } from 'vitest';

import Skeleton from '../src/lib/components/Skeleton.svelte';

afterEach(() => {
  cleanup();
});

describe('Skeleton', () => {
  it('renders a div by default', () => {
    const { container } = render(Skeleton);
    const node = container.querySelector('.skeleton');
    expect(node?.tagName).toBe('DIV');
  });

  it('renders a span when block=false', () => {
    const { container } = render(Skeleton, { props: { block: false } });
    const node = container.querySelector('.skeleton');
    expect(node?.tagName).toBe('SPAN');
  });

  it('honors width / height / radius props as inline styles', () => {
    const { container } = render(Skeleton, {
      props: { width: '50%', height: '2rem', radius: '12px' },
    });
    const node = container.querySelector('.skeleton') as HTMLElement;
    expect(node.style.width).toBe('50%');
    expect(node.style.height).toBe('2rem');
    expect(node.style.borderRadius).toBe('12px');
  });

  it('is hidden from assistive tech (aria-hidden=true)', () => {
    const { container } = render(Skeleton);
    const node = container.querySelector('.skeleton');
    expect(node?.getAttribute('aria-hidden')).toBe('true');
  });
});
