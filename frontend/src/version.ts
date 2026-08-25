// Build version of this frontend bundle (version-display REQ-3.5).
// deploy.yml's deploy-swa job stamps VITE_BUILD_VERSION=$GITHUB_SHA at build time (same
// pattern as VITE_API_BASE_URL); local/CI builds carry no stamp -> DEV_VERSION.

export const DEV_VERSION = "dev";
// Backend reported ok without a version field (older backend) — REQ-3.3.
export const UNKNOWN_VERSION = "unknown";
// Backend version not available (health still loading, or failed) — REQ-3.2.
export const MISSING_VERSION = "—";

const SHORT_SHA_LENGTH = 7;

export function buildVersion(): string {
  return import.meta.env.VITE_BUILD_VERSION || DEV_VERSION;
}

export function shortSha(sha: string): string {
  return sha.slice(0, SHORT_SHA_LENGTH);
}
