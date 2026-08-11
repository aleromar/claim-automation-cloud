import { afterEach, describe, expect, it, vi } from "vitest";

import { buildVersion, DEV_VERSION, shortSha } from "./version";

const FULL_SHA = "884ac8adeadbeef0123456789abcdef012345678";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("buildVersion (version-display REQ-3.5)", () => {
  it("falls back to DEV_VERSION when VITE_BUILD_VERSION is unset", () => {
    // Stub the absence too: a VITE_BUILD_VERSION exported in a dev shell or a
    // gitignored frontend/.env must not redden this test (review gate R-3).
    vi.stubEnv("VITE_BUILD_VERSION", undefined);
    expect(buildVersion()).toBe(DEV_VERSION);
  });

  it("returns the stamped SHA when VITE_BUILD_VERSION is set", () => {
    vi.stubEnv("VITE_BUILD_VERSION", FULL_SHA);
    expect(buildVersion()).toBe(FULL_SHA);
  });

  it("treats an empty stamp as unset", () => {
    vi.stubEnv("VITE_BUILD_VERSION", "");
    expect(buildVersion()).toBe(DEV_VERSION);
  });
});

describe("shortSha (version-display REQ-3.1)", () => {
  it("truncates a full SHA to its 7-char short form", () => {
    expect(shortSha(FULL_SHA)).toBe("884ac8a");
  });

  it('leaves values shorter than 7 chars (e.g. "dev") unchanged', () => {
    expect(shortSha(DEV_VERSION)).toBe(DEV_VERSION);
  });
});
