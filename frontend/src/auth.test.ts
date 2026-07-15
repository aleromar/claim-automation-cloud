// REQ-4: fragment consumption (strip before render), sessionStorage, authFetch.
import { afterEach, describe, expect, it, vi } from "vitest";

import { authFetch, clearToken, consumeFragment, getToken } from "./auth";

afterEach(() => {
  sessionStorage.clear();
  window.location.hash = "";
  history.replaceState(null, "", "/");
  vi.restoreAllMocks();
});

describe("consumeFragment", () => {
  it("moves #token=… into sessionStorage and strips the URL (REQ-4.1)", () => {
    window.location.hash = "#token=jwt-abc";
    const { error } = consumeFragment();
    expect(error).toBeNull();
    expect(getToken()).toBe("jwt-abc");
    expect(window.location.hash).toBe("");
  });

  it("surfaces #error=… without storing a token (REQ-4.4)", () => {
    window.location.hash = "#error=unauthorized";
    const { error } = consumeFragment();
    expect(error).toBe("unauthorized");
    expect(getToken()).toBeNull();
    expect(window.location.hash).toBe("");
  });

  it("is a no-op without a fragment", () => {
    const { error } = consumeFragment();
    expect(error).toBeNull();
    expect(getToken()).toBeNull();
  });
});

describe("token store", () => {
  it("clearToken removes the stored token", () => {
    window.location.hash = "#token=jwt-abc";
    consumeFragment();
    clearToken();
    expect(getToken()).toBeNull();
  });
});

describe("authFetch", () => {
  it("sends the token as a Bearer header (REQ-4.2)", async () => {
    window.location.hash = "#token=jwt-abc";
    consumeFragment();
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("{}", { status: 200 }));
    await authFetch("/api/me");
    const headers = new Headers(spy.mock.calls[0][1]?.headers);
    expect(headers.get("Authorization")).toBe("Bearer jwt-abc");
  });

  it("sends no Authorization header without a token", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("{}", { status: 200 }));
    await authFetch("/api/me");
    const headers = new Headers(spy.mock.calls[0][1]?.headers);
    expect(headers.get("Authorization")).toBeNull();
  });

  it("clears the stored token when the API answers 401 (REQ-4.3)", async () => {
    window.location.hash = "#token=jwt-stale";
    consumeFragment();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("", { status: 401 }),
    );
    const res = await authFetch("/api/me");
    expect(res.status).toBe(401);
    expect(getToken()).toBeNull();
  });
});
