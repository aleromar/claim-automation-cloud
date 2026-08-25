// Live-run configuration: required env vars (GitHub Actions secrets in CI,
// e2e/.env.live locally) validated once, up front — a missing credential must
// fail the suite in seconds, not after a webServer boot.
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
] as const;

export type LiveEnvKey = (typeof REQUIRED_ENV)[number];

export function requireLiveEnv(): Record<LiveEnvKey, string> {
  const missing = REQUIRED_ENV.filter((name) => !process.env[name]);
  if (missing.length > 0) {
    throw new Error(
      `live e2e: missing env vars: ${missing.join(", ")} — ` +
        "fill e2e/.env.live (see .env.live.example) or the CI secrets",
    );
  }
  return Object.fromEntries(
    REQUIRED_ENV.map((name) => [name, process.env[name] as string]),
  ) as Record<LiveEnvKey, string>;
}
