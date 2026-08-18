import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { consumeFragment, getToken } from "./auth";

const OPERATOR = "operator@example.com";

function storeToken() {
  window.location.hash = "#token=jwt-abc";
  consumeFragment();
}

function mockApi({
  me = new Response(JSON.stringify({ email: OPERATOR }), { status: 200 }),
  health = new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
  // Factory, not a shared Response: the worker panel re-fetches status after
  // actions and a Response body reads only once (worker-controls gate ER-W4).
  worker = () =>
    new Response(JSON.stringify({ enabled: false, heartbeat: null }), {
      status: 200,
    }),
  settings = () =>
    new Response(
      JSON.stringify({
        trello: {
          api_key_stored: false,
          token_stored: false,
          board_id: "",
          list_id: "",
        },
        gmail: { account_email: OPERATOR, refresh_token_stored: false },
      }),
      { status: 200 },
    ),
}: {
  me?: Response | Promise<Response>;
  health?: Response | Promise<Response>;
  worker?: () => Response | Promise<Response>;
  settings?: () => Response | Promise<Response>;
} = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("/api/me")) return me;
    if (url.includes("/api/health")) return health;
    if (url.includes("/api/worker/status")) return worker();
    if (url.includes("/api/settings")) return settings();
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
    const fetchSpy = mockApi();
    render(<App />);
    await waitFor(() => expect(screen.getByText(OPERATOR)).toBeInTheDocument());
    const fetchCallsBeforeLogout = fetchSpy.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: /log out/i }));

    expect(
      screen.getByRole("link", { name: /sign in with google/i }),
    ).toBeInTheDocument();
    expect(getToken()).toBeNull();
    // Client-side only (no server revocation): logout must issue no API call.
    expect(fetchSpy.mock.calls.length).toBe(fetchCallsBeforeLogout);
  });

  it("renders the worker panel on the dashboard (worker-controls REQ-4.1)", async () => {
    storeToken();
    mockApi();
    render(<App />);
    expect(
      await screen.findByRole("switch", { name: /worker enabled/i }),
    ).toBeInTheDocument();
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

describe("App navigation (settings REQ-4.1) and authed error banner (REQ-4.8)", () => {
  // BrowserRouter mutates real history — leave each test back on "/".
  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("navigates Dashboard ⇄ Settings; worker controls stay on Dashboard", async () => {
    storeToken();
    mockApi();
    render(<App />);
    await screen.findByRole("switch", { name: /worker enabled/i });

    fireEvent.click(screen.getByRole("link", { name: /settings/i }));
    expect(
      await screen.findByRole("heading", { name: /settings/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("switch")).toBeNull();

    fireEvent.click(screen.getByRole("link", { name: /dashboard/i }));
    expect(
      await screen.findByRole("switch", { name: /worker enabled/i }),
    ).toBeInTheDocument();
  });

  it("surfaces a fragment error to a still-authed operator (REQ-4.8)", async () => {
    // A failed reconnect redirects with #error= while the stored JWT is still
    // valid — without this banner the failure is invisible (P10 gate finding).
    storeToken();
    mockApi();
    render(<App initialError="login_failed" />);
    await waitFor(() => expect(screen.getByText(OPERATOR)).toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent(/google.*failed/i);
  });

  it("renders fixed banner copy, never the fragment's free text", async () => {
    // Anyone can craft /#error=<free text>; the banner is trusted UI, so the
    // code is mapped to fixed copy and the raw value never rendered.
    storeToken();
    mockApi();
    render(<App initialError="EVIL_FREE_TEXT" />);
    await waitFor(() => expect(screen.getByText(OPERATOR)).toBeInTheDocument());
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/google.*failed/i);
    expect(alert).not.toHaveTextContent(/EVIL_FREE_TEXT/);
  });

  it("routes an unknown path back to the dashboard, not an empty main", async () => {
    // navigationFallback serves the SPA for any typo'd deep link — without a
    // catch-all route that renders header + nothing.
    window.history.replaceState(null, "", "/tpyo");
    storeToken();
    mockApi();
    render(<App />);
    expect(
      await screen.findByRole("switch", { name: /worker enabled/i }),
    ).toBeInTheDocument();
  });
});

describe("App version footer (version-display REQ-3)", () => {
  const FULL_SHA = "884ac8adeadbeef0123456789abcdef012345678";

  // The `frontend dev` asserts assume no ambient VITE_BUILD_VERSION (dev
  // shell export or gitignored frontend/.env) — stub the absence (R-3).
  beforeEach(() => {
    vi.stubEnv("VITE_BUILD_VERSION", undefined);
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("shows both short versions when health reports a backend version (REQ-3.1)", async () => {
    storeToken();
    mockApi({
      health: new Response(
        JSON.stringify({ status: "ok", version: FULL_SHA }),
        { status: 200 },
      ),
    });
    render(<App />);
    const footer = await screen.findByRole("contentinfo");
    // waitFor: the footer mounts before the mocked health fetch settles, so
    // the backend slot briefly shows the placeholder.
    await waitFor(() => expect(footer).toHaveTextContent(/backend 884ac8a/));
    expect(footer).toHaveTextContent(/frontend dev/);
  });

  it("shows a placeholder for the backend version while health is not ok (REQ-3.2)", async () => {
    storeToken();
    mockApi({ health: new Response("", { status: 503 }) });
    render(<App />);
    const footer = await screen.findByRole("contentinfo");
    await waitFor(() => expect(footer).toHaveTextContent(/backend —/));
    expect(footer).toHaveTextContent(/frontend dev/);
  });

  it('shows "unknown" when health is ok but carries no version (REQ-3.3)', async () => {
    storeToken();
    mockApi(); // default health body: {"status":"ok"} with no version field
    render(<App />);
    const footer = await screen.findByRole("contentinfo");
    await waitFor(() => expect(footer).toHaveTextContent(/backend unknown/));
    // The ok-without-version body must not flip the health line to error.
    expect(screen.queryByText(/backend unavailable/i)).toBeNull();
  });

  it("renders no footer on the login screen (REQ-3.4)", () => {
    render(<App />);
    expect(
      screen.getByRole("link", { name: /sign in with google/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("contentinfo")).toBeNull();
  });
});

describe("App health status inside the authenticated dashboard (walking-skeleton REQ-2)", () => {
  it("renders no health banner when the backend is healthy (only problems surface)", async () => {
    // REQ-2.1 superseded 2026-08-18 (operator): healthy = silent; the footer
    // version is the ok-path proof of the health fetch.
    storeToken();
    mockApi();
    render(<App />);
    const footer = await screen.findByRole("contentinfo");
    await waitFor(() => expect(footer).toHaveTextContent(/backend unknown/));
    expect(screen.queryByText(/all good/i)).toBeNull();
    expect(screen.queryByText(/backend unavailable/i)).toBeNull();
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
