import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import WorkerControls from "./WorkerControls";

type HeartbeatBody = { at: string; status: string } | null;

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
        /skipped_no_access/,
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
