import { defineConfig, devices } from "@playwright/test";

import { requireLiveEnv } from "./tests-live/helpers/env";

// LIVE suite (live-e2e spec; retargeted by staging-environment REQ-4): runs
// CI-only against the DEPLOYED staging environment — no local servers, no
// Azurite, no secret-file seeding. No stub IdP either: the suite injects a
// pre-minted session JWT (tests-live/helpers/session.ts) signed with staging's
// real key (fetched from Key Vault by the workflow). Runs as the deploy gate
// on push to main; one run at a time (workflow concurrency group).

const env = requireLiveEnv(); // fail fast before any test starts

export default defineConfig({
  testDir: "./tests-live",
  workers: 1, // one real mailbox — parallelism is a race by construction
  retries: process.env.CI ? 1 : 0, // sweep + per-attempt refs make a retry safe
  forbidOnly: !!process.env.CI,
  // POST /api/worker/run is synchronous (worker_routes.py) and the pipeline
  // budget is 120s after two network preflights — default timeouts would kill
  // a healthy run. The run-result expect carries its own 200s budget in-test.
  timeout: 300_000,
  // Deployed target: each API roundtrip costs 0.5–2.5s on cold Consumption
  // (KV fetch per request), so a click→POST→status-refetch cycle overruns the
  // 5s default — the first staging gate run failed exactly there (Bugfix log).
  expect: { timeout: 15_000 },
  use: {
    baseURL: env.LIVE_FRONTEND_URL,
    // The trace captures a JWT signed with staging's REAL key (1h TTL). It
    // stays on the ephemeral runner — the workflow uploads no artifacts.
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
