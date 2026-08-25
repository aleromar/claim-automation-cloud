import { defineConfig, devices } from "@playwright/test";

import { requireLiveEnv } from "./tests-live/helpers/env";

// LIVE suite (live-e2e spec): real Gmail dev account + real Trello test board;
// backend + Vite are the same local stack as the PR e2e — but NO stub IdP:
// the suite injects a pre-minted session JWT (tests-live/helpers/session.ts),
// so google_token_url points at real Google for the pipeline's token refresh.
// Runs on push to main (deploy.yml gates deploys on it) and locally via
// `make e2e-live` (mutual exclusion with CI — see spec Operational constraints).

const env = requireLiveEnv(); // fail fast before any webServer boots

export default defineConfig({
  testDir: "./tests-live",
  globalSetup: "./tests-live/global-setup.ts",
  workers: 1, // one real mailbox — parallelism is a race by construction
  retries: process.env.CI ? 1 : 0, // sweep + per-attempt refs make a retry safe
  forbidOnly: !!process.env.CI,
  // POST /api/worker/run is synchronous (worker_routes.py) and the pipeline
  // budget is 120s after two network preflights — default timeouts would kill
  // a healthy run. The run-result expect carries its own 200s budget in-test.
  timeout: 300_000,
  use: {
    baseURL: "http://localhost:5173",
    trace: "on-first-retry", // captures the test JWT only (fixed local key)
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      // Seed-then-start, PR-e2e pattern; separate secrets file so the two
      // suites can never poison each other's stores.
      command:
        "sh -c 'node ../e2e/seed-secrets-live.mjs && uv run uvicorn app.main:app --port 8000'",
      cwd: "../backend",
      url: "http://localhost:8000/api/health",
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        GOOGLE_AUTH_URL: "https://accounts.google.com/o/oauth2/v2/auth",
        GOOGLE_TOKEN_URL: "https://oauth2.googleapis.com/token",
        GOOGLE_CLIENT_ID: env.GOOGLE_CLIENT_ID,
        OAUTH_REDIRECT_URI: "http://localhost:8000/api/auth/callback",
        OPERATOR_EMAIL: env.GMAIL_ACCOUNT,
        FRONTEND_BASE_URL: "http://localhost:5173",
        SECRET_STORE_BACKEND: "file",
        SECRET_STORE_FILE_PATH: "../e2e/.tmp/secrets-live.json",
        // Pin storage to local Azurite (worker-controls REQ-7.4 rationale).
        TABLE_STORAGE_BACKEND: "connection_string",
        STORAGE_CONNECTION_STRING: "UseDevelopmentStorage=true",
      },
    },
    {
      command: "npm run dev -- --port 5173 --strictPort",
      cwd: "../frontend",
      url: "http://localhost:5173",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
