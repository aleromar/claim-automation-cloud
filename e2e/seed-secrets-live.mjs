// Seeds the LIVE suite's file secret store (separate from PR e2e's secrets.json)
// before uvicorn starts. Real values come from the environment — GitHub Actions
// secrets in CI, e2e/.env.live locally; fails fast if any is missing so a
// misconfigured run dies here, not mid-pipeline. Trello credentials are NOT
// file-seeded: the test writes them through POST /api/settings/trello.
import { mkdirSync, writeFileSync } from "node:fs";

const required = ["GOOGLE_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"];
const missing = required.filter((name) => !process.env[name]);
if (missing.length > 0) {
  console.error(`seed-secrets-live: missing env vars: ${missing.join(", ")}`);
  process.exit(1);
}

mkdirSync(new URL("./.tmp/", import.meta.url), { recursive: true });
// 0600: real credentials on disk — same posture as the backend FileSecretStore.
writeFileSync(
  new URL("./.tmp/secrets-live.json", import.meta.url),
  JSON.stringify({
    // Fixed test value — the JWT helper reads it back from this file, so the
    // seeder is the single source. Worthless outside a local live run.
    "session-signing-key": "live-e2e-signing-key-0123456789abcdef0123456789abcdef",
    "google-client-secret": process.env.GOOGLE_CLIENT_SECRET,
    "gmail-refresh-token": process.env.GMAIL_REFRESH_TOKEN,
  }),
  { mode: 0o600 },
);
