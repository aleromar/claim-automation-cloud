// Pre-minted session JWT (live-e2e REQ, grill decision 1): the live suite has
// no login step — logging in through the stub IdP would overwrite the real
// gmail-refresh-token, and google_token_url is shared with GmailClient. The
// signing key is read from the seeded secret store, so the seeder stays the
// single source; claims mirror app/security.py:mint_session_jwt exactly
// ({email, iat, exp}, HS256 — verify requires exp+iat and the operator email).

import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import type { Page } from "@playwright/test";

import { requireLiveEnv } from "./env";

export const LIVE_SECRETS_PATH = fileURLToPath(
  new URL("../../.tmp/secrets-live.json", import.meta.url),
);

const b64url = (value: string | Buffer): string =>
  Buffer.from(value).toString("base64url");

export function mintSessionJwt(): string {
  const signingKey = (
    JSON.parse(readFileSync(LIVE_SECRETS_PATH, "utf8")) as Record<string, string>
  )["session-signing-key"];
  const now = Math.floor(Date.now() / 1000);
  const header = b64url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const payload = b64url(
    JSON.stringify({
      email: requireLiveEnv().GMAIL_ACCOUNT,
      iat: now,
      exp: now + 3600,
    }),
  );
  const signature = createHmac("sha256", signingKey)
    .update(`${header}.${payload}`)
    .digest("base64url");
  return `${header}.${payload}.${signature}`;
}

/** Plant the JWT before first paint — the app boots authed from sessionStorage. */
export async function injectSession(page: Page, jwt: string): Promise<void> {
  await page.addInitScript((token: string) => {
    sessionStorage.setItem("session_jwt", token);
  }, jwt);
}
