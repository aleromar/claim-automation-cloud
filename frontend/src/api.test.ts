// Deployment REQ-4.2: API base URL — relative in dev, absolute (VITE_API_BASE_URL) in prod.

import { afterEach, describe, expect, it, vi } from "vitest";

import { apiUrl } from "./api";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("apiUrl", () => {
  it("returns the path unchanged when VITE_API_BASE_URL is unset (dev: Vite proxy)", () => {
    expect(apiUrl("/api/health")).toBe("/api/health");
  });

  it("prefixes the configured base URL (prod: cross-origin Function App)", () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://func-claim.azurewebsites.net");
    expect(apiUrl("/api/health")).toBe(
      "https://func-claim.azurewebsites.net/api/health",
    );
  });

  it("tolerates a trailing slash on the base URL", () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://func-claim.azurewebsites.net/");
    expect(apiUrl("/api/me")).toBe(
      "https://func-claim.azurewebsites.net/api/me",
    );
  });
});
