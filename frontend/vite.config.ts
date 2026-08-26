/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev proxy: the SPA calls a relative "/api/*"; Vite forwards it to the backend
// so no backend URL is ever hardcoded (matches the SWA "/api" route in prod).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: "./src/setupTests.ts",
    // Local-time bucket helpers (metrics-dashboard gate M3): pin the zone so
    // the same test reads the same day on CI (UTC) and the laptop (CET).
    env: { TZ: "Europe/Madrid" },
  },
});
