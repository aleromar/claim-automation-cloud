import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("App health status", () => {
  it('shows "All good" when the backend health check returns ok (REQ-2.1)', async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
    );
    render(<App />);
    await waitFor(() =>
      expect(screen.getByText(/all good/i)).toBeInTheDocument(),
    );
  });

  it("shows an error state when the health check fails (REQ-2.2)", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network down"));
    render(<App />);
    await waitFor(() =>
      expect(screen.getByText(/backend unavailable/i)).toBeInTheDocument(),
    );
  });

  it("shows an error state on a non-ok HTTP status (REQ-2.2)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("", { status: 503 }),
    );
    render(<App />);
    await waitFor(() =>
      expect(screen.getByText(/backend unavailable/i)).toBeInTheDocument(),
    );
  });

  it("shows an error state on a 200 with an unexpected body (REQ-2.2)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "degraded" }), { status: 200 }),
    );
    render(<App />);
    await waitFor(() =>
      expect(screen.getByText(/backend unavailable/i)).toBeInTheDocument(),
    );
  });

  it("shows a loading indicator while the request is in flight (REQ-2.3)", () => {
    vi.spyOn(globalThis, "fetch").mockReturnValue(new Promise(() => {}));
    render(<App />);
    expect(screen.getByText(/loading|checking/i)).toBeInTheDocument();
  });
});
