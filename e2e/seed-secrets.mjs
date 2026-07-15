// Seeds the e2e file secret store BEFORE uvicorn starts (its lifespan fails
// fast without a session signing key). Overwrites any previous run's state.
import { mkdirSync, writeFileSync } from "node:fs";

mkdirSync(new URL("./.tmp/", import.meta.url), { recursive: true });
writeFileSync(
  new URL("./.tmp/secrets.json", import.meta.url),
  JSON.stringify({
    "session-signing-key": "e2e-signing-key-0123456789abcdef0123456789abcdef",
    "google-client-secret": "e2e-client-secret",
  }),
);
