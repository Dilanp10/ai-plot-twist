/**
 * Vitest global setup — runs before every test file.
 *
 * @testing-library/svelte automatically cleans up mounted components after
 * each test when ``cleanup`` is called. The ``afterEach`` hook here ensures
 * the DOM is reset between tests even when tests don't use Testing Library
 * helpers directly.
 */
import { cleanup } from "@testing-library/svelte";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});
