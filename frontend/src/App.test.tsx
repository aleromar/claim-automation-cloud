import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import App from "./App";
import { consumeFragment } from "./auth";

const OPERATOR = "operator@example.com";

function storeToken() {
  window.location.hash = "#token=jwt-abc";
  consumeFragment();
}

function mockApi({
  me = new Response(JSON.stringify({ email: OPERATOR }), { status: 200 }),
  health = new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
}: {
  me?: Response | Promise<Response>;
  health?: Response | Promise<Response>;
} = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("/api/me")) return me;
    if (url.includes("/api/health")) return health;
    throw new Error(`unexpected fetch: ${url}`);
  });
}

describe("App authentication gate (REQ-1.1, REQ-4)", () => {
  it("shows the login screen and calls no API when no token is stored", () => {
    const spy = vi.spyOn(globalThis, "fetch");
    render(<App />);
    expect(
      screen.getByRole("link", { name: /sign in with google/i }),
    ).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
  });

  it("renders the dashboard with the operator email when /api/me accepts the token", async () => {
    storeToken();
    mockApi();
    render(<App />);
    await waitFor(() => expect(screen.getByText(OPERATOR)).toBeInTheDocument());
  });

  it("falls back to the login screen when the session is rejected (REQ-4.3)", async () => {
    storeToken();
    mockApi({ me: new Response("", { status: 401 }) });
    render(<App />);
    await waitFor(() =>
      expect(
        screen.getByRole("link", { name: /sign in with google/i }),
      ).toBeInTheDocument(),
    );
  });

  it("shows the login error carried in the fragment (REQ-4.4)", () => {
    render(<App initialError="unauthorized" />);
    expect(screen.getByRole("alert")).toHaveTextContent(/not authorized/i);
  });

  it("logs out: clears the stored token and shows the login screen (REQ-4.5/4.6)", async () => {
    storeToken();
    mockApi();
    render(<App />);
    await waitFor(() => expect(screen.getByText(OPERATOR)).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /log out/i }));

    expect(
      screen.getByRole("link", { name: /sign in with google/i }),
    ).toBeInTheDocument();
    expect(sessionStorage.getItem("session_jwt")).toBeNull();
  });

  it("shows a session-checking state while /api/me is in flight", () => {
    storeToken();
    mockApi({ me: new Promise<Response>(() => {}) });
    render(<App />);
    expect(screen.getByText(/checking session/i)).toBeInTheDocument();
  });

  it("shows a clear error when /api/me returns 200 without an email", async () => {
    storeToken();
    mockApi({ me: new Response(JSON.stringify({}), { status: 200 }) });
    render(<App />);
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        /session check returned an unexpected response/i,
      ),
    );
  });
});

describe("App health status inside the authenticated dashboard (walking-skeleton REQ-2)", () => {
  it('shows "All good" when the backend health check returns ok', async () => {
    storeToken();
    mockApi();
    render(<App />);
    await waitFor(() =>
      expect(screen.getByText(/all good/i)).toBeInTheDocument(),
    );
  });

  it("shows an error state on a non-ok HTTP status", async () => {
    storeToken();
    mockApi({ health: new Response("", { status: 503 }) });
    render(<App />);
    await waitFor(() =>
      expect(screen.getByText(/backend unavailable/i)).toBeInTheDocument(),
    );
  });

  it("shows an error state on a 200 with an unexpected body", async () => {
    storeToken();
    mockApi({
      health: new Response(JSON.stringify({ status: "degraded" }), {
        status: 200,
      }),
    });
    render(<App />);
    await waitFor(() =>
      expect(screen.getByText(/backend unavailable/i)).toBeInTheDocument(),
    );
  });

  it("shows a loading indicator while the health request is in flight", async () => {
    storeToken();
    mockApi({ health: new Promise<Response>(() => {}) });
    render(<App />);
    await waitFor(() =>
      expect(screen.getByText(/checking backend/i)).toBeInTheDocument(),
    );
  });
});
