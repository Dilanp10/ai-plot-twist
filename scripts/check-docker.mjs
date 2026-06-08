#!/usr/bin/env node
/**
 * Pre-flight: assert Docker daemon is running.
 *
 * Called by `pnpm db:up` (which is called by `pnpm dev`) so the developer
 * gets a clear, actionable error instead of a cryptic socket message.
 *
 * Cross-platform (Windows + mac + Linux). No npm dependencies — uses only
 * Node.js built-ins so it works before `pnpm install` has finished.
 *
 * Spec edge-case: "if Docker is not running, pnpm dev MUST fail with a
 * message instructing the developer to start Docker."
 */
import { execFileSync } from "node:child_process";

try {
  execFileSync("docker", ["info"], { stdio: "ignore" });
} catch {
  process.stderr.write(
    "\n❌  Docker no está corriendo.\n" +
      "    Iniciá Docker Desktop y volvé a correr `pnpm dev`.\n\n",
  );
  process.exit(1);
}
