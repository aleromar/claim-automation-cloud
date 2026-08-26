import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import MetricsPanel from "./MetricsPanel";
import {
  CHART_ARIA_LABEL,
  GRANULARITY_LABEL,
  METRICS_UNAVAILABLE,
  NO_CLAIMS_IN_WINDOW,
  SERIES_ERRORS,
  TILE_CARDS_CREATED,
  TILE_EMAILS_FAILED,
  TILE_EMAILS_PROCESSED,
  TILE_FAILED_RUNS,
  TIME_WINDOW_LABEL,
} from "./strings";

// metrics-dashboard REQ-2/3/4: one fetch on mount; tiles are all-time; the
// window preset re-derives chart + list locally (no refetch).

// Mirrors the ClaimOut wire contract — no subject (dropped server-side, W2).
type Claim = {
  at: string;
  claim_ref: string;
  type: string;
  town: string | null;
  owner: string | null;
  card_url: string;
};

const daysAgo = (days: number) =>
  new Date(Date.now() - days * 86_400_000).toISOString();

const recentClaim: Claim = {
  at: daysAgo(1),
  claim_ref: "2026/417",
  type: "DECLARACION_SINIESTRO",
  town: "Madrid",
  owner: "Nombre Apellido",
  card_url: "https://trello.com/c/abc",
};

const oldClaim: Claim = {
  at: "2024-05-01T08:00:00+00:00",
  claim_ref: "2024/12",
  type: "SOLICITUD_ASISTENCIA",
  town: null,
  owner: null,
  card_url: "https://trello.com/c/old",
};

const mockMetrics = (
  body: unknown = {
    emails_processed: 7,
    cards_created: 2,
    emails_failed: 3,
    failed_runs: 1,
    error_runs: [{ at: daysAgo(2), failed: 3 }],
    claims: [recentClaim, oldClaim],
  },
  status = 200,
) =>
  vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(new Response(JSON.stringify(body), { status }));

describe("MetricsPanel tiles (REQ-2/8)", () => {
  it("shows the four all-time counters", async () => {
    mockMetrics();
    render(<MetricsPanel />);
    expect(
      (await screen.findByText(new RegExp(TILE_EMAILS_PROCESSED, "i")))
        .textContent,
    ).toMatch(/7/);
    expect(
      screen.getByText(new RegExp(TILE_CARDS_CREATED, "i")).textContent,
    ).toMatch(/2/);
    expect(
      screen.getByText(new RegExp(TILE_EMAILS_FAILED, "i")).textContent,
    ).toMatch(/3/);
    expect(
      screen.getByText(new RegExp(TILE_FAILED_RUNS, "i")).textContent,
    ).toMatch(/1/);
  });

  it("shows a visible error state when the fetch fails", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("down"));
    render(<MetricsPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      METRICS_UNAVAILABLE,
    );
  });

  it("treats a 200 with a broken contract as an error, not success", async () => {
    mockMetrics({ emails_processed: "many", cards_created: 2, claims: [] });
    render(<MetricsPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      METRICS_UNAVAILABLE,
    );
  });

  it("treats missing error fields as a broken contract (delta REQ-7)", async () => {
    mockMetrics({ emails_processed: 7, cards_created: 2, claims: [] });
    render(<MetricsPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      METRICS_UNAVAILABLE,
    );
  });

  it("treats a claim with an unparseable date as a broken contract (Gate 3 C1)", async () => {
    // A NaN date would silently blank the chart while the table still lists
    // the claim — fail loud instead.
    mockMetrics({
      emails_processed: 1,
      cards_created: 1,
      claims: [{ ...recentClaim, at: "not-a-date" }],
    });
    render(<MetricsPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      METRICS_UNAVAILABLE,
    );
  });

  it("treats a non-ok response as an error (Gate 3 W3)", async () => {
    mockMetrics({ detail: "boom" }, 500);
    render(<MetricsPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      METRICS_UNAVAILABLE,
    );
  });
});

describe("MetricsPanel window + list (REQ-3/4)", () => {
  it("defaults to 30 days / daily: recent claim listed, old claim excluded", async () => {
    mockMetrics();
    render(<MetricsPanel />);
    expect(await screen.findByRole("link", { name: "2026/417" })).toBeVisible();
    expect(
      screen.queryByRole("link", { name: "2024/12" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: TIME_WINDOW_LABEL }),
    ).toHaveValue("30d");
    expect(
      screen.getByRole("combobox", { name: GRANULARITY_LABEL }),
    ).toHaveValue("day");
  });

  it("snaps granularity to the new window's default and constrains its options (REQ-9.1)", async () => {
    mockMetrics();
    render(<MetricsPanel />);
    await screen.findByRole("link", { name: "2026/417" });
    fireEvent.change(
      screen.getByRole("combobox", { name: TIME_WINDOW_LABEL }),
      {
        target: { value: "1y" },
      },
    );
    const granularity = screen.getByRole("combobox", {
      name: GRANULARITY_LABEL,
    });
    expect(granularity).toHaveValue("week");
    const options = [...granularity.querySelectorAll("option")].map((o) =>
      o.getAttribute("value"),
    );
    expect(options).toEqual(["week", "month"]);
  });

  it("links each claim to its Trello card in a new tab", async () => {
    mockMetrics();
    render(<MetricsPanel />);
    const link = await screen.findByRole("link", { name: "2026/417" });
    expect(link).toHaveAttribute("href", "https://trello.com/c/abc");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    // Columns: date WITH time of day (delta REQ-10), ref, type, town, owner.
    const row = link.closest("tr");
    expect(row).toHaveTextContent(new Date(recentClaim.at).toLocaleString());
    expect(row).toHaveTextContent("DECLARACION_SINIESTRO");
    expect(row).toHaveTextContent("Madrid");
    expect(row).toHaveTextContent("Nombre Apellido");
  });

  it("switching the preset re-windows without refetching", async () => {
    const spy = mockMetrics();
    render(<MetricsPanel />);
    await screen.findByRole("link", { name: "2026/417" });
    fireEvent.change(
      screen.getByRole("combobox", { name: TIME_WINDOW_LABEL }),
      {
        target: { value: "all" },
      },
    );
    expect(await screen.findByRole("link", { name: "2024/12" })).toBeVisible();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("shows an explicit empty message when the window has no claims (REQ-4.2)", async () => {
    mockMetrics({
      emails_processed: 0,
      cards_created: 0,
      emails_failed: 0,
      failed_runs: 0,
      error_runs: [],
      claims: [],
    });
    render(<MetricsPanel />);
    expect(
      await screen.findByText(new RegExp(NO_CLAIMS_IN_WINDOW, "i")),
    ).toBeVisible();
  });

  it("renders the two-series chart for the selected window", async () => {
    mockMetrics();
    render(<MetricsPanel />);
    await waitFor(() =>
      expect(
        screen.getByRole("img", { name: CHART_ARIA_LABEL }),
      ).toBeInTheDocument(),
    );
    // Legend proves the error series is wired in (delta REQ-9.2).
    expect(screen.getByText(SERIES_ERRORS)).toBeInTheDocument();
  });
});
