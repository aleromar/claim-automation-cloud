import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import ReconnectBanner from "./ReconnectBanner";
import { RECONNECT_GMAIL } from "./strings";

// metrics-dashboard REQ-5: latest-heartbeat rule. Absence is the healthy
// default — the banner renders nothing unless the last heartbeat says
// skipped_no_access; WorkerControls owns the visible error states.

const mockStatus = (body: unknown, status = 200) =>
  vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(new Response(JSON.stringify(body), { status }));

const renderBanner = () =>
  render(
    <MemoryRouter>
      <ReconnectBanner />
    </MemoryRouter>,
  );

describe("ReconnectBanner", () => {
  it("shows the banner with a Settings link when the latest run was skipped_no_access", async () => {
    mockStatus({
      enabled: true,
      heartbeat: {
        at: "2026-08-25T10:00:00+00:00",
        status: "skipped_no_access",
      },
    });
    renderBanner();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(RECONNECT_GMAIL);
    const link = screen.getByRole("link", { name: RECONNECT_GMAIL });
    expect(link).toHaveAttribute("href", "/settings");
  });

  it("renders nothing for a healthy run", async () => {
    const spy = mockStatus({
      enabled: true,
      heartbeat: { at: "2026-08-25T10:00:00+00:00", status: "ran" },
    });
    renderBanner();
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders nothing when no run has happened yet", async () => {
    const spy = mockStatus({ enabled: false, heartbeat: null });
    renderBanner();
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders nothing on a non-ok response (Gate 3 W3)", async () => {
    const spy = mockStatus({ detail: "boom" }, 500);
    renderBanner();
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders nothing when the status fetch fails", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValue(new Error("down"));
    renderBanner();
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
