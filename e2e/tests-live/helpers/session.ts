// Pre-minted session JWT (live-e2e REQ, grill decision 1): the live suite has
// no login step — logging in through the OAuth flow would overwrite the real
// gmail-refresh-token in staging's Key Vault. The signing key arrives as
// SESSION_SIGNING_KEY, fetched from staging Key Vault by the deploy workflow
// (staging-environment REQ-4.2); claims mirror app/security.py:mint_session_jwt
// exactly ({email, iat, exp}, HS256 — verify requires exp+iat and the operator
// email, which in staging IS the dev Gmail account).

import { createHmac } from "node:crypto";

import type { Page } from "@playwright/test";

import { requireLiveEnv } from "./env";

const b64url = (value: string | Buffer): string =>
  Buffer.from(value).toString("base64url");

export function mintSessionJwt(): string {
  const signingKey = requireLiveEnv().SESSION_SIGNING_KEY;
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
