/**
 * Component tests for TwistModal (T-013).
 *
 * Mocks twistStore to keep the tests offline + deterministic.
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import TwistModal from '../src/lib/components/TwistModal.svelte';

// ---------------------------------------------------------------------------
// twistStore mock (hoisted)
// ---------------------------------------------------------------------------

const { mockSubmit, mockStore } = vi.hoisted(() => {
  const mockSubmit = vi.fn();
  const mockStore = {
    submit: mockSubmit,
    errorMessage: null as string | null,
  };
  return { mockSubmit, mockStore };
});

vi.mock('../src/lib/twist-store.svelte', () => ({ twistStore: mockStore }));

const CHAPTER_ID = '11111111-1111-1111-1111-111111111111';

beforeEach(() => {
  mockSubmit.mockReset();
  mockStore.errorMessage = null;
});

afterEach(() => {
  cleanup();
});

// ---------------------------------------------------------------------------

describe('TwistModal', () => {
  it('does not render when open=false', () => {
    render(TwistModal, {
      props: { open: false, chapterId: CHAPTER_ID, onClose: vi.fn() },
    });
    expect(screen.queryByText('Tirá una idea')).toBeNull();
  });

  it('renders when open=true', () => {
    render(TwistModal, {
      props: { open: true, chapterId: CHAPTER_ID, onClose: vi.fn() },
    });
    expect(screen.getByText('Tirá una idea')).toBeTruthy();
  });

  it('disables submit when content is too short', () => {
    render(TwistModal, {
      props: { open: true, chapterId: CHAPTER_ID, onClose: vi.fn() },
    });
    const submitBtn = screen.getByRole('button', {
      name: /Tirá la idea/i,
    }) as HTMLButtonElement;
    expect(submitBtn.disabled).toBe(true);
  });

  it('calls twistStore.submit and onClose on success', async () => {
    const onClose = vi.fn();
    mockSubmit.mockResolvedValue(true);

    render(TwistModal, {
      props: { open: true, chapterId: CHAPTER_ID, onClose },
    });

    const textarea = screen.getByLabelText(/Tu idea/i) as HTMLTextAreaElement;
    await fireEvent.input(textarea, {
      target: { value: 'Una idea suficientemente larga' },
    });

    const submitBtn = screen.getByRole('button', { name: /Tirá la idea/i });
    await fireEvent.click(submitBtn);

    expect(mockSubmit).toHaveBeenCalledWith(
      CHAPTER_ID,
      'Una idea suficientemente larga',
    );
    expect(onClose).toHaveBeenCalled();
  });

  it('shows the error pill and keeps the modal open on submit failure', async () => {
    const onClose = vi.fn();
    mockSubmit.mockResolvedValue(false);
    mockStore.errorMessage = 'Ya usaste tus 3 ideas para este capítulo.';

    render(TwistModal, {
      props: { open: true, chapterId: CHAPTER_ID, onClose },
    });

    const textarea = screen.getByLabelText(/Tu idea/i) as HTMLTextAreaElement;
    await fireEvent.input(textarea, {
      target: { value: 'Otra idea suficientemente larga' },
    });
    await fireEvent.click(
      screen.getByRole('button', { name: /Tirá la idea/i }),
    );

    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByText(/3 ideas/i)).toBeTruthy();
  });

  it('cancel button calls onClose without submitting', async () => {
    const onClose = vi.fn();
    render(TwistModal, {
      props: { open: true, chapterId: CHAPTER_ID, onClose },
    });
    await fireEvent.click(
      screen.getByRole('button', { name: /Cancelar/i }),
    );
    expect(onClose).toHaveBeenCalled();
    expect(mockSubmit).not.toHaveBeenCalled();
  });
});
