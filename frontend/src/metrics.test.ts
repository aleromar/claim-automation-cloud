// metrics-dashboard delta REQ-9: window × granularity helpers. Local-time by
// design (TZ pinned to Europe/Madrid via vite.config test.env).
import {
  buildBuckets,
  bucketKey,
  claimsInWindow,
  defaultGranularity,
  GRANULARITY_MATRIX,
  WINDOWS,
} from "./metrics";

const claim = (atIso: string) => ({ at: atIso });
// Fixed "now": Tuesday 2026-08-25 10:00 local (Madrid, UTC+2 in August).
const NOW = new Date(2026, 7, 25, 10, 0, 0);

describe("window × granularity matrix (REQ-9.1)", () => {
  it("exposes the closed window set in display order", () => {
    expect(WINDOWS).toEqual(["1d", "7d", "30d", "90d", "1y", "all"]);
  });

  it("constrains granularities per window, first = default", () => {
    expect(GRANULARITY_MATRIX).toEqual({
      "1d": ["hour"],
      "7d": ["hour", "day"],
      "30d": ["day", "week"],
      "90d": ["day", "week"],
      "1y": ["week", "month"],
      all: ["month"],
    });
    expect(defaultGranularity("7d")).toBe("hour");
    expect(defaultGranularity("30d")).toBe("day");
    expect(defaultGranularity("all")).toBe("month");
  });
});

describe("bucketKey", () => {
  it("buckets hours and days in LOCAL time, not UTC", () => {
    // 22:30Z on the 25th is already 00:30 on the 26th in Madrid (UTC+2).
    expect(bucketKey(new Date("2026-08-25T22:30:00Z"), "hour")).toBe(
      "2026-08-26 00:00",
    );
    expect(bucketKey(new Date("2026-08-25T22:30:00Z"), "day")).toBe(
      "2026-08-26",
    );
  });

  it("keys days, weeks and months", () => {
    const d = new Date(2026, 7, 25, 12);
    expect(bucketKey(d, "day")).toBe("2026-08-25");
    expect(bucketKey(d, "month")).toBe("2026-08");
  });

  it("handles the ISO-week year boundary (gate M3)", () => {
    // Week 1 of 2026 starts Mon 2025-12-29 (Jan 1 2026 is a Thursday).
    expect(bucketKey(new Date(2025, 11, 30, 12), "week")).toBe("2026-W01");
    // 2026 has 53 ISO weeks; Fri 2027-01-01 still belongs to 2026-W53.
    expect(bucketKey(new Date(2027, 0, 1, 12), "week")).toBe("2026-W53");
    // A full Mon–Sun week shares one key; the next Monday starts a new one.
    expect(bucketKey(new Date(2026, 7, 24), "week")).toBe(
      bucketKey(new Date(2026, 7, 30), "week"),
    );
    expect(bucketKey(new Date(2026, 7, 31), "week")).not.toBe(
      bucketKey(new Date(2026, 7, 30), "week"),
    );
  });
});

describe("buildBuckets", () => {
  it("zero-fills 24 hourly buckets for 1d, ending at the current hour", () => {
    const buckets = buildBuckets([], "1d", "hour", NOW);
    expect(buckets).toHaveLength(24);
    expect(buckets[0].key).toBe("2026-08-24 11:00");
    expect(buckets[23].key).toBe("2026-08-25 10:00");
  });

  it("zero-fills 168 hourly buckets for 7d", () => {
    const buckets = buildBuckets([], "7d", "hour", NOW);
    expect(buckets).toHaveLength(168);
    expect(new Set(buckets.map((b) => b.key)).size).toBe(168);
  });

  it("pins DST days (delta gate E3): fall-back day has 24 keys, spring-forward 23", () => {
    // Madrid fall-back: Sun 2026-10-25 (25 real hours; both 02:00 instants
    // share one key — accepted). Spring-forward: Sun 2026-03-29 (23 hours).
    const fallBack = buildBuckets(
      [],
      "1d",
      "hour",
      new Date(2026, 9, 25, 23, 30),
    );
    expect(fallBack).toHaveLength(24);
    expect(new Set(fallBack.map((b) => b.key)).size).toBe(24);
    const spring = buildBuckets(
      [],
      "1d",
      "hour",
      new Date(2026, 2, 29, 23, 30),
    );
    expect(spring).toHaveLength(23);
    expect(spring.some((b) => b.key === "2026-03-29 02:00")).toBe(false);
  });

  it("zero-fills 30 daily buckets ending today", () => {
    const buckets = buildBuckets([], "30d", "day", NOW);
    expect(buckets).toHaveLength(30);
    expect(buckets[0].key).toBe("2026-07-27");
    expect(buckets[29].key).toBe("2026-08-25");
  });

  it("zero-fills weekly buckets covering the full year for 1y", () => {
    // 365 days back lands mid-week; aligning to the Monday grid extends the
    // window, so this fixture covers 53 week buckets (52–53 in general).
    const buckets = buildBuckets([], "1y", "week", NOW);
    expect(buckets).toHaveLength(53);
    expect(new Set(buckets.map((b) => b.key)).size).toBe(53);
    expect(buckets[52].key).toBe(bucketKey(NOW, "week"));
  });

  it("counts claims into their local buckets and ignores out-of-window rows", () => {
    const buckets = buildBuckets(
      [
        claim("2026-08-25T08:00:00Z"),
        claim("2026-08-24T22:30:00Z"), // 00:30 local on the 25th
        claim("2026-06-01T08:00:00Z"), // outside 30d
      ],
      "30d",
      "day",
      NOW,
    );
    const byKey = Object.fromEntries(buckets.map((b) => [b.key, b.count]));
    expect(byKey["2026-08-25"]).toBe(2);
    expect(buckets.reduce((sum, b) => sum + b.count, 0)).toBe(2);
  });

  it("weighs items via weightOf (error runs sum their failed counts, REQ-9.2)", () => {
    const buckets = buildBuckets(
      [
        { at: "2026-08-25T08:00:00Z", failed: 2 },
        { at: "2026-08-25T09:00:00Z", failed: 1 },
      ],
      "30d",
      "day",
      NOW,
      (run) => run.failed,
    );
    const byKey = Object.fromEntries(buckets.map((b) => [b.key, b.count]));
    expect(byKey["2026-08-25"]).toBe(3);
  });

  it("'all' with no items falls back to a single current-month bucket (REQ-3.4)", () => {
    expect(buildBuckets([], "all", "month", NOW)).toEqual([
      { key: "2026-08", count: 0 },
    ]);
  });

  it("'all' honors an explicit shared earliest over the items' own (PR review fix)", () => {
    // Two series must share ONE timeline: without the override, each series
    // would start at its own earliest and the chart indexes would misalign.
    const buckets = buildBuckets(
      [claim("2026-08-20T08:00:00Z")],
      "all",
      "month",
      NOW,
      () => 1,
      new Date(2026, 5, 10),
    );
    expect(buckets.map((b) => b.key)).toEqual([
      "2026-06",
      "2026-07",
      "2026-08",
    ]);
    expect(buckets.map((b) => b.count)).toEqual([0, 0, 1]);
  });

  it("'all' spans months from the earliest item to now", () => {
    const buckets = buildBuckets(
      [claim("2026-06-10T08:00:00Z"), claim("2026-08-20T08:00:00Z")],
      "all",
      "month",
      NOW,
    );
    expect(buckets.map((b) => b.key)).toEqual([
      "2026-06",
      "2026-07",
      "2026-08",
    ]);
    expect(buckets.map((b) => b.count)).toEqual([1, 0, 1]);
  });
});

describe("claimsInWindow", () => {
  const claims = [
    claim("2026-08-25T08:00:00Z"),
    claim("2026-07-01T08:00:00Z"),
    claim("2025-01-01T08:00:00Z"),
  ];

  it("keeps only items at or after the aligned window start", () => {
    expect(claimsInWindow(claims, "30d", "day", NOW)).toHaveLength(1);
    expect(claimsInWindow(claims, "90d", "day", NOW)).toHaveLength(2);
  });

  it("'all' keeps everything", () => {
    expect(claimsInWindow(claims, "all", "month", NOW)).toHaveLength(3);
  });
});
