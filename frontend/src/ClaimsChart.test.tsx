import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ClaimsChart from "./ClaimsChart";
import { CHART_ARIA_LABEL } from "./strings";

// Two-series grouped bars (delta REQ-9.2): legend mandatory for ≥2 series,
// none for one; per-bucket <title> carries every series' value; the claims
// list stays the table view (dataviz method).

const keys = ["2026-08-23", "2026-08-24", "2026-08-25"];
const twoSeries = [
  {
    name: "Claims",
    color: "var(--pico-primary)",
    buckets: [
      { key: keys[0], count: 0 },
      { key: keys[1], count: 2 },
      { key: keys[2], count: 1 },
    ],
  },
  {
    name: "Failed emails",
    color: "#d97706",
    buckets: [
      { key: keys[0], count: 1 },
      { key: keys[1], count: 0 },
      { key: keys[2], count: 0 },
    ],
  },
];

describe("ClaimsChart (two series)", () => {
  it("renders an accessible chart with a legend naming both series", () => {
    render(<ClaimsChart series={twoSeries} />);
    expect(
      screen.getByRole("img", { name: CHART_ARIA_LABEL }),
    ).toBeInTheDocument();
    expect(screen.getByText("Claims")).toBeInTheDocument();
    expect(screen.getByText("Failed emails")).toBeInTheDocument();
  });

  it("puts every series' value in each bucket tooltip", () => {
    const { container } = render(<ClaimsChart series={twoSeries} />);
    const titles = [...container.querySelectorAll("title")].map(
      (t) => t.textContent,
    );
    expect(titles).toEqual([
      "2026-08-23 — Claims: 0, Failed emails: 1",
      "2026-08-24 — Claims: 2, Failed emails: 0",
      "2026-08-25 — Claims: 1, Failed emails: 0",
    ]);
  });

  it("draws one bar per non-zero series value", () => {
    const { container } = render(<ClaimsChart series={twoSeries} />);
    expect(container.querySelectorAll("path")).toHaveLength(3);
  });

  it("renders no legend for a single series", () => {
    render(<ClaimsChart series={[twoSeries[0]]} />);
    expect(screen.queryByText("Claims")).not.toBeInTheDocument();
  });

  it("survives all-zero series without NaN geometry (REQ-3.4 carry-over)", () => {
    const zero = twoSeries.map((s) => ({
      ...s,
      buckets: s.buckets.map((b) => ({ ...b, count: 0 })),
    }));
    const { container } = render(<ClaimsChart series={zero} />);
    expect(container.innerHTML).not.toContain("NaN");
    expect(container.querySelectorAll("path")).toHaveLength(0);
  });

  it("survives 168 dense buckets without NaN or negative geometry (gate E2)", () => {
    const dense = (fill: number) =>
      Array.from({ length: 168 }, (_, i) => ({
        key: `k${i}`,
        count: i % 7 === 0 ? fill : 0,
      }));
    const { container } = render(
      <ClaimsChart
        series={[
          { name: "Claims", color: "var(--pico-primary)", buckets: dense(3) },
          { name: "Failed emails", color: "#d97706", buckets: dense(1) },
        ]}
      />,
    );
    expect(container.innerHTML).not.toContain("NaN");
    expect(container.querySelectorAll("path").length).toBe(48);
  });

  it("labels the window edges selectively, in text tokens", () => {
    render(<ClaimsChart series={twoSeries} />);
    expect(screen.getByText("2026-08-23")).toBeInTheDocument();
    expect(screen.getByText("2026-08-25")).toBeInTheDocument();
    expect(screen.queryByText("2026-08-24")).not.toBeInTheDocument();
  });
});
