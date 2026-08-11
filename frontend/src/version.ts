// Build version of this frontend bundle (version-display REQ-3.5).
// deploy-swa.yml stamps VITE_BUILD_VERSION=$GITHUB_SHA at build time (same
// pattern as VITE_API_BASE_URL); local/CI builds carry no stamp -> DEV_VERSION.

export const DEV_VERSION = "dev";

const SHORT_SHA_LENGTH = 7;

export function buildVersion(): string {
  return import.meta.env.VITE_BUILD_VERSION || DEV_VERSION;
}

export function shortSha(sha: string): string {
  return sha.slice(0, SHORT_SHA_LENGTH);
}
