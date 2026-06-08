/**
 * Application version, read from apps/web/package.json at build time.
 *
 * Vite bundles this import statically; no runtime fetch is involved.
 * Update apps/web/package.json version → rebuild to reflect it here.
 */
import packageJson from "../../package.json";

export const VERSION: string = packageJson.version;
