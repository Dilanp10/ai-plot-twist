/**
 * Smoke test for the App component.
 *
 * Module 001 / Task T-017.
 *
 * Verifies that:
 *   1. App mounts without throwing.
 *   2. The documented placeholder strings are present in the rendered DOM.
 *
 * This is an intentionally thin test — it exists to confirm the component
 * tree renders and to gate CI from day one (Gate 8 — tests from day one).
 * Module 010 will add richer interaction tests as the UI grows.
 */
import { render, screen } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";
import App from "./App.svelte";

describe("App", () => {
  it("mounts without throwing", () => {
    expect(() => render(App)).not.toThrow();
  });

  it("renders the app title", () => {
    render(App);
    // The <h1> inside App.svelte must contain the project name.
    expect(screen.getByRole("heading", { level: 1 })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("AI Plot Twist");
  });

  it("renders the bootstrap tagline", () => {
    render(App);
    // The documented placeholder string from spec FR-009 / plan.md.
    const tagline = screen.getByText(/bootstrap OK/i);
    expect(tagline).toBeTruthy();
  });

  it("renders a version string", () => {
    render(App);
    // Version comes from apps/web/package.json via lib/version.ts.
    // We just assert a version-shaped string (vX.Y.Z) is present.
    const version = screen.getByText(/v\d+\.\d+\.\d+/);
    expect(version).toBeTruthy();
  });
});
