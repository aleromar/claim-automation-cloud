// Live-run configuration (CI-only since staging-environment REQ-4): the eight
// third-party credentials come from GitHub repo secrets; the target URLs from
// the `staging` GitHub environment's variables; SESSION_SIGNING_KEY from the
// workflow's Key Vault fetch. Validated once, up front — a missing value must
// fail the suite in seconds.
// The Gmail account is env-configured too (Gate 3 #14): this repo is public,
// and the address should not be the one personal datum committed in it.

export const REQUIRED_ENV = [
  "GMAIL_ACCOUNT",
  "GOOGLE_CLIENT_ID",
  "GOOGLE_CLIENT_SECRET",
  "GMAIL_REFRESH_TOKEN",
  "TRELLO_API_KEY",
  "TRELLO_TOKEN",
  "TRELLO_BOARD_ID",
  "TRELLO_LIST_ID",
  // Deployed-staging targets + JWT key (staging-environment REQ-4):
  "LIVE_FRONTEND_URL",
  "LIVE_BACKEND_API_URL",
  "SESSION_SIGNING_KEY",
] as const;

export type LiveEnvKey = (typeof REQUIRED_ENV)[number];

export function requireLiveEnv(): Record<LiveEnvKey, string> {
  const missing = REQUIRED_ENV.filter((name) => !process.env[name]);
  if (missing.length > 0) {
    throw new Error(
      `live e2e: missing env vars: ${missing.join(", ")} — ` +
        "CI-only suite: check the deploy workflow's secrets/vars " +
        "(.env.live.example inventories the gh-secret set)",
    );
  }
  return Object.fromEntries(
    REQUIRED_ENV.map((name) => [name, process.env[name] as string]),
  ) as Record<LiveEnvKey, string>;
}
