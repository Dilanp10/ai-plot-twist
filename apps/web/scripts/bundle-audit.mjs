#!/usr/bin/env node
/**
 * Bundle-size audit — fails CI when the main route bundle exceeds the
 * gzipped budget.
 *
 * Module 010 / Task T-014.
 *
 * Runs after `pnpm build`. Walks `dist/assets/`, classifies each chunk:
 *   - main (eager) — the index-*.js produced from main.ts
 *   - lazy (code-split) — every other js chunk
 *   - vendor — workbox-window, virtual_pwa-register, etc.
 *
 * Budgets:
 *   - main:    80 KB gz (FR-006)
 *   - any one lazy chunk: 40 KB gz
 *
 * Exit code 1 (with a clear report) when a budget is exceeded.
 *
 * Usage::
 *
 *     pnpm audit:bundle           # runs after build
 *     pnpm audit:bundle --json    # machine-readable for CI dashboards
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { gzipSync } from "node:zlib";
import path from "node:path";

const DIST = path.resolve(import.meta.dirname, "..", "dist", "assets");

const BUDGETS = {
  main_kb_gz: 80,
  lazy_kb_gz: 40,
};

const VENDOR_PATTERNS = [/^workbox-/, /^virtual_pwa-/];

function gzKb(filePath) {
  const buf = readFileSync(filePath);
  return gzipSync(buf, { level: 9 }).length / 1024;
}

function classify(name) {
  if (VENDOR_PATTERNS.some((p) => p.test(name))) return "vendor";
  if (name.startsWith("index-") && name.endsWith(".js")) return "main";
  if (name.endsWith(".js")) return "lazy";
  return "asset";
}

function main() {
  let files;
  try {
    files = readdirSync(DIST);
  } catch {
    console.error(`[bundle-audit] ${DIST} not found. Run \`pnpm build\` first.`);
    process.exit(1);
  }

  const report = files
    .filter((f) => f.endsWith(".js"))
    .map((name) => {
      const full = path.join(DIST, name);
      const sz = statSync(full).size / 1024;
      const gz = gzKb(full);
      return { name, kind: classify(name), kb_raw: sz, kb_gz: gz };
    })
    .sort((a, b) => b.kb_gz - a.kb_gz);

  const main = report.find((r) => r.kind === "main");
  const violations = [];

  if (main && main.kb_gz > BUDGETS.main_kb_gz) {
    violations.push({
      kind: "main",
      name: main.name,
      kb_gz: main.kb_gz,
      budget: BUDGETS.main_kb_gz,
    });
  }
  for (const r of report.filter((r) => r.kind === "lazy")) {
    if (r.kb_gz > BUDGETS.lazy_kb_gz) {
      violations.push({
        kind: "lazy",
        name: r.name,
        kb_gz: r.kb_gz,
        budget: BUDGETS.lazy_kb_gz,
      });
    }
  }

  if (process.argv.includes("--json")) {
    console.log(JSON.stringify({ report, violations, budgets: BUDGETS }, null, 2));
  } else {
    console.log("Bundle audit (gzipped):");
    for (const r of report) {
      const flag = violations.find((v) => v.name === r.name) ? "  ✗" : "   ";
      console.log(
        `${flag} ${r.kind.padEnd(7)} ${r.kb_gz.toFixed(2).padStart(7)} KB gz  ${r.name}`,
      );
    }
    console.log(
      `\nBudgets: main ≤ ${BUDGETS.main_kb_gz} KB gz, each lazy ≤ ${BUDGETS.lazy_kb_gz} KB gz`,
    );
  }

  if (violations.length > 0) {
    console.error(
      `\n[bundle-audit] ${violations.length} budget violation(s). Failing.`,
    );
    process.exit(1);
  }
}

main();
