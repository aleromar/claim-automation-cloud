import { defineConfig, devices } from "@playwright/test";

// Boots the REAL backend (uvicorn) and frontend (Vite) plus a local stub IdP —
// the one documented exception to "no mocks" (structure.md, 2026-07-14).
// Vite's dev proxy forwards /api/* from :5173 to the backend on :8000; the
// OAuth callback hits :8000 directly, then redirects back to :5173.
export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  use: {
    baseURL: "http://localhost:5173",
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "node stub-idp.mjs",
      cwd: ".",
      url: "http://localhost:9100/healthz",
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      // Seed-then-start: the lifespan fail-fast needs the signing key in place.
      // Never reuse an existing dev uvicorn — it lacks the stub-IdP env.
      command: "sh -c 'node ../e2e/seed-secrets.mjs && uv run uvicorn app.main:app --port 8000'",
      cwd: "../backend",
      url: "http://localhost:8000/api/health",
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        GOOGLE_AUTH_URL: "http://localhost:9100/authorize",
        GOOGLE_TOKEN_URL: "http://localhost:9100/token",
        GOOGLE_CLIENT_ID: "e2e-client",
        OAUTH_REDIRECT_URI: "http://localhost:8000/api/auth/callback",
        OPERATOR_EMAIL: "operator@example.com",
        FRONTEND_BASE_URL: "http://localhost:5173",
        SECRET_STORE_BACKEND: "file",
        SECRET_STORE_FILE_PATH: "../e2e/.tmp/secrets.json",
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
