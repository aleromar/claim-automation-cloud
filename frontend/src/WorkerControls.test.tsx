import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import WorkerControls from "./WorkerControls";

type HeartbeatBody = {
  at: string;
  status: string;
  matched?: number | null;
  processed?: number | null;
  failed?: number | null;
  failed_total?: number | null;
} | null;

const statusResponse = (enabled: boolean, heartbeat: HeartbeatBody) => () =>
  new Response(JSON.stringify({ enabled, heartbeat }), { status: 200 });

// Factories, not shared Response objects: the panel re-fetches status after
// actions, and a Response body can only be read once (worker-controls gate ER-W4).
function mockWorkerApi({
  status = statusResponse(false, null),
  enabled = (body: { enabled: boolean }) =>
    new Response(JSON.stringify(body), { status: 200 }),
  run = () =>
    new Response(JSON.stringify({ outcome: "skipped_disabled" }), {
      status: 200,
    }),
}: {
  status?: () => Response | Promise<Response>;
  enabled?: (body: { enabled: boolean }) => Response | Promise<Response>;
  run?: () => Response | Promise<Response>;
} = {}) {
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.includes("/api/worker/status")) return status();
      if (url.includes("/api/worker/enabled"))
        return enabled(JSON.parse(String(init?.body)));
      if (url.includes("/api/worker/run")) return run();
      throw new Error(`unexpected fetch: ${url}`);
    });
}

const statusCalls = (spy: ReturnType<typeof mockWorkerApi>) =>
  spy.mock.calls.filter(([input]) =>
    String(input).includes("/api/worker/status"),
  ).length;

describe("WorkerControls status display (REQ-4.1/4.2)", () => {
  it("renders the switch Off and an explicit never-ran state for a fresh store", async () => {
    mockWorkerApi();
    render(<WorkerControls />);
    const workerSwitch = await screen.findByRole("switch", {
      name: /worker enabled/i,
    });
    expect(workerSwitch).not.toBeChecked();
    expect(screen.getByText(/last run:/i)).toHaveTextContent(/never/i);
  });

  it("renders the last-run timestamp and outcome label", async () => {
    const at = "2026-08-15T10:30:00+00:00";
    mockWorkerApi({
      status: statusResponse(true, { at, status: "skipped_disabled" }),
    });
    render(<WorkerControls />);
    const lastRun = await screen.findByText(/last run:/i);
    expect(lastRun).toHaveTextContent(new Date(at).toLocaleString());
    expect(lastRun).toHaveTextContent(/skipped \(worker off\)/i);
    expect(
      screen.getByRole("switch", { name: /worker enabled/i }),
    ).toBeChecked();
  });

  it("renders the Gmail-reconnect label for skipped_no_access (gmail-client REQ-5)", async () => {
    mockWorkerApi({
      status: statusResponse(true, {
        at: "2026-08-21T10:30:00+00:00",
        status: "skipped_no_access",
      }),
    });
    render(<WorkerControls />);
    const lastRun = await screen.findByText(/last run:/i);
    expect(lastRun).toHaveTextContent(/skipped \(gmail needs reconnect\)/i);
  });

  it("renders the matched count next to a ran heartbeat (gmail-client REQ-5)", async () => {
    mockWorkerApi({
      status: statusResponse(true, {
        at: "2026-08-21T10:30:00+00:00",
        status: "ran",
        matched: 3,
      }),
    });
    render(<WorkerControls />);
    const lastRun = await screen.findByText(/last run:/i);
    expect(lastRun).toHaveTextContent(/ran — 3 matching emails/i);
  });

  it("renders a singular label for exactly one matched email", async () => {
    mockWorkerApi({
      status: statusResponse(true, {
        at: "2026-08-21T10:30:00+00:00",
        status: "ran",
        matched: 1,
      }),
    });
    render(<WorkerControls />);
    const lastRun = await screen.findByText(/last run:/i);
    expect(lastRun).toHaveTextContent(/ran — 1 matching email(?!s)/i);
  });

  it("treats a non-numeric matched as a broken contract (error state)", async () => {
    mockWorkerApi({
      status: () =>
        new Response(
          JSON.stringify({
            enabled: true,
            heartbeat: {
              at: "2026-08-21T10:30:00+00:00",
              status: "ran",
              matched: "3",
            },
          }),
          { status: 200 },
        ),
    });
    render(<WorkerControls />);
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        /worker status unavailable/i,
      ),
    );
  });

  it("renders a zero matched count (0 is a successful probe, not absence)", async () => {
    mockWorkerApi({
      status: statusResponse(true, {
        at: "2026-08-21T10:30:00+00:00",
        status: "ran",
        matched: 0,
      }),
    });
    render(<WorkerControls />);
    const lastRun = await screen.findByText(/last run:/i);
    expect(lastRun).toHaveTextContent(/ran — 0 matching emails/i);
  });

  it("renders no count when matched is absent (older backends / pre-5b rows)", async () => {
    mockWorkerApi({
      status: statusResponse(true, {
        at: "2026-08-21T10:30:00+00:00",
        status: "ran",
      }),
    });
    render(<WorkerControls />);
    const lastRun = await screen.findByText(/last run:/i);
    expect(lastRun).toHaveTextContent(/ran/i);
    expect(lastRun).not.toHaveTextContent(/matching emails/i);
  });

  it("renders no count when matched is null (non-ran outcomes)", async () => {
    mockWorkerApi({
      status: statusResponse(true, {
        at: "2026-08-21T10:30:00+00:00",
        status: "failed",
        matched: null,
      }),
    });
    render(<WorkerControls />);
    const lastRun = await screen.findByText(/last run:/i);
    expect(lastRun).not.toHaveTextContent(/matching emails/i);
  });

  it("shows the error state when the status fetch fails", async () => {
    mockWorkerApi({ status: () => new Response("", { status: 503 }) });
    render(<WorkerControls />);
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        /worker status unavailable/i,
      ),
    );
  });

  it("treats a 200 with a broken contract as the error state", async () => {
    mockWorkerApi({
      status: () =>
        new Response(JSON.stringify({ enabled: "yes", heartbeat: null }), {
          status: 200,
        }),
    });
    render(<WorkerControls />);
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        /worker status unavailable/i,
      ),
    );
  });
});

describe("WorkerControls toggle (REQ-4.3)", () => {
  it("posts the target state and renders the returned state", async () => {
    const spy = mockWorkerApi();
    render(<WorkerControls />);
    const workerSwitch = await screen.findByRole("switch", {
      name: /worker enabled/i,
    });
    fireEvent.click(workerSwitch);
    await waitFor(() => expect(workerSwitch).toBeChecked());
    const post = spy.mock.calls.find(([input]) =>
      String(input).includes("/api/worker/enabled"),
    );
    expect(post?.[1]?.method).toBe("POST");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ enabled: true });
  });

  it("disables the controls while the toggle is in flight", async () => {
    mockWorkerApi({ enabled: () => new Promise<Response>(() => {}) });
    render(<WorkerControls />);
    const workerSwitch = await screen.findByRole("switch", {
      name: /worker enabled/i,
    });
    fireEvent.click(workerSwitch);
    await waitFor(() => expect(workerSwitch).toBeDisabled());
    expect(screen.getByRole("button", { name: /process now/i })).toBeDisabled();
  });

  it("shows an inline error and re-fetches status when the toggle fails", async () => {
    const spy = mockWorkerApi({
      enabled: () => new Response("", { status: 500 }),
    });
    render(<WorkerControls />);
    fireEvent.click(
      await screen.findByRole("switch", { name: /worker enabled/i }),
    );
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/action failed/i),
    );
    // Initial load + post-failure refresh: never assume the write landed.
    expect(statusCalls(spy)).toBe(2);
  });
});

describe("WorkerControls process-now (REQ-4.4/4.5)", () => {
  it("shows the returned outcome label and refreshes status", async () => {
    const spy = mockWorkerApi();
    render(<WorkerControls />);
    fireEvent.click(
      await screen.findByRole("button", { name: /process now/i }),
    );
    await waitFor(() =>
      expect(screen.getByText(/run result:/i)).toHaveTextContent(
        /skipped \(worker off\)/i,
      ),
    );
    expect(statusCalls(spy)).toBe(2);
  });

  it("renders an unknown outcome value raw instead of crashing (REQ-4.5)", async () => {
    // Exemplar changed at the gmail-client gate (X2): skipped_no_access is a
    // KNOWN outcome now — any still-unknown string proves the degrade rule.
    mockWorkerApi({
      run: () =>
        new Response(JSON.stringify({ outcome: "paused_maintenance" }), {
          status: 200,
        }),
    });
    render(<WorkerControls />);
    fireEvent.click(
      await screen.findByRole("button", { name: /process now/i }),
    );
    await waitFor(() =>
      expect(screen.getByText(/run result:/i)).toHaveTextContent(
        /paused_maintenance/,
      ),
    );
  });

  it("labels a skipped_no_access run result (gmail-client REQ-5)", async () => {
    mockWorkerApi({
      run: () =>
        new Response(JSON.stringify({ outcome: "skipped_no_access" }), {
          status: 200,
        }),
    });
    render(<WorkerControls />);
    fireEvent.click(
      await screen.findByRole("button", { name: /process now/i }),
    );
    await waitFor(() =>
      expect(screen.getByText(/run result:/i)).toHaveTextContent(
        /skipped \(gmail needs reconnect\)/i,
      ),
    );
  });

  it("clears a previous run result when the switch is toggled (review L2)", async () => {
    // A lingering "Run result" from before a toggle describes a run under the
    // OLD enabled state — misleading, so toggling must clear it.
    mockWorkerApi();
    render(<WorkerControls />);
    fireEvent.click(
      await screen.findByRole("button", { name: /process now/i }),
    );
    await waitFor(() =>
      expect(screen.getByText(/run result:/i)).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole("switch", { name: /worker enabled/i }));
    await waitFor(() => expect(screen.queryByText(/run result:/i)).toBeNull());
  });

  it("shows an inline error and re-fetches status when the run 500s", async () => {
    const spy = mockWorkerApi({ run: () => new Response("", { status: 500 }) });
    render(<WorkerControls />);
    fireEvent.click(
      await screen.findByRole("button", { name: /process now/i }),
    );
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/action failed/i),
    );
    // The heartbeat row carries the truth (`failed`) — the refresh surfaces it.
    expect(statusCalls(spy)).toBe(2);
  });
});

describe("run counts and the failed-state gauge (pipeline-wiring REQ-10)", () => {
  it("renders processed/failed counts with the gauge on a ran heartbeat", async () => {
    mockWorkerApi({
      status: statusResponse(true, {
        at: "2026-08-21T12:00:00Z",
        status: "ran",
        processed: 3,
        failed: 1,
        failed_total: 2,
      }),
    });
    render(<WorkerControls />);
    await screen.findByText(/3 processed, 1 failed · 2 in failed state/);
  });

  it("renders zero counts (0 is a successful, informative run)", async () => {
    mockWorkerApi({
      status: statusResponse(true, {
        at: "2026-08-21T12:00:00Z",
        status: "ran",
        processed: 0,
        failed: 0,
        failed_total: 0,
      }),
    });
    render(<WorkerControls />);
    await screen.findByText(/0 processed, 0 failed · 0 in failed state/);
  });

  it("renders the gauge cap as 100+", async () => {
    mockWorkerApi({
      status: statusResponse(true, {
        at: "2026-08-21T12:00:00Z",
        status: "ran",
        processed: 1,
        failed: 0,
        failed_total: 101,
      }),
    });
    render(<WorkerControls />);
    await screen.findByText(/100\+ in failed state/);
  });

  it("omits the gauge segment when failed_total is null (gauge unavailable)", async () => {
    mockWorkerApi({
      status: statusResponse(true, {
        at: "2026-08-21T12:00:00Z",
        status: "ran",
        processed: 2,
        failed: 0,
        failed_total: null,
      }),
    });
    render(<WorkerControls />);
    await screen.findByText(/2 processed, 0 failed$/);
    expect(screen.queryByText(/in failed state/)).toBeNull();
  });

  it("keeps rendering the legacy matched line on pre-5c rows", async () => {
    mockWorkerApi({
      status: statusResponse(true, {
        at: "2026-08-21T12:00:00Z",
        status: "ran",
        matched: 3,
      }),
    });
    render(<WorkerControls />);
    await screen.findByText(/3 matching emails/);
  });

  it("labels a skipped_busy heartbeat (REQ-12)", async () => {
    mockWorkerApi({
      status: statusResponse(true, {
        at: "2026-08-21T12:00:00Z",
        status: "skipped_busy",
      }),
    });
    render(<WorkerControls />);
    await screen.findByText(/skipped \(another run in progress\)/);
  });
});
