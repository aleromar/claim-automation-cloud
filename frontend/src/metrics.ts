// metrics-dashboard delta REQ-9: pure window × granularity helpers for the
// dashboard chart. Deliberately LOCAL-time (the operator's clock decides what
// "today" means); tests pin TZ via vite.config.ts test.env. No date library —
// day/hour/ISO-week/month keys and stepping, all DST-safe via the Date
// constructor (DST behavior fixture-pinned: 24 keys on the 25-hour fall-back
// day, 23 on spring-forward — delta gate E3).

export const WINDOWS = ["1d", "7d", "30d", "90d", "1y", "all"] as const;
export type Window = (typeof WINDOWS)[number];
export type Granularity = "hour" | "day" | "week" | "month";

// Constrained combos (delta REQ-9.1); first entry = the window's default.
export const GRANULARITY_MATRIX: Record<Window, readonly Granularity[]> = {
  "1d": ["hour"],
  "7d": ["hour", "day"],
  "30d": ["day", "week"],
  "90d": ["day", "week"],
  "1y": ["week", "month"],
  all: ["month"],
};

export const defaultGranularity = (window: Window): Granularity =>
  GRANULARITY_MATRIX[window][0];

export type Bucket = { key: string; count: number };
type Dated = { at: string };

// Window length in days for the bounded windows ("all" derives from data).
const WINDOW_DAYS: Record<Exclude<Window, "all">, number> = {
  "1d": 1,
  "7d": 7,
  "30d": 30,
  "90d": 90,
  "1y": 365,
};

const pad = (n: number) => String(n).padStart(2, "0");

const startOfHour = (d: Date) =>
  new Date(d.getFullYear(), d.getMonth(), d.getDate(), d.getHours());
const startOfDay = (d: Date) =>
  new Date(d.getFullYear(), d.getMonth(), d.getDate());
const addHours = (d: Date, hours: number) =>
  new Date(d.getFullYear(), d.getMonth(), d.getDate(), d.getHours() + hours);
const addDays = (d: Date, days: number) =>
  new Date(d.getFullYear(), d.getMonth(), d.getDate() + days);
// Monday-start weeks (ISO 8601).
const startOfIsoWeek = (d: Date) =>
  addDays(startOfDay(d), -((d.getDay() + 6) % 7));
const startOfMonth = (d: Date) => new Date(d.getFullYear(), d.getMonth(), 1);

const dayKey = (d: Date) =>
  `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const hourKey = (d: Date) => `${dayKey(d)} ${pad(d.getHours())}:00`;
const monthKey = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;
const isoWeekKey = (d: Date) => {
  // The week's Thursday owns both the ISO year and the week number (this is
  // what makes the Dec/Jan boundary come out right — gate M3).
  const thursday = addDays(startOfIsoWeek(d), 3);
  // Jan 4 is always inside week 1 of its ISO year.
  const firstThursday = addDays(
    startOfIsoWeek(new Date(thursday.getFullYear(), 0, 4)),
    3,
  );
  // round(): absorbs the ±1h DST drift of local-midnight arithmetic.
  const week =
    1 +
    Math.round(
      (thursday.getTime() - firstThursday.getTime()) / (7 * 86_400_000),
    );
  return `${thursday.getFullYear()}-W${pad(week)}`;
};

export const bucketKey = (date: Date, granularity: Granularity): string => {
  if (granularity === "hour") return hourKey(date);
  if (granularity === "week") return isoWeekKey(date);
  if (granularity === "month") return monthKey(date);
  return dayKey(date);
};

const startOfBucket = (date: Date, granularity: Granularity): Date => {
  if (granularity === "hour") return startOfHour(date);
  if (granularity === "week") return startOfIsoWeek(date);
  if (granularity === "month") return startOfMonth(date);
  return startOfDay(date);
};

const advance = (cursor: Date, granularity: Granularity): Date => {
  if (granularity === "hour") return addHours(cursor, 1);
  if (granularity === "week") return addDays(cursor, 7);
  if (granularity === "month")
    return new Date(cursor.getFullYear(), cursor.getMonth() + 1, 1);
  return addDays(cursor, 1);
};

// Bucket-aligned start of the window. "all" starts at the earliest item's
// bucket, falling back to the current bucket when no items exist (REQ-3.4).
export const windowStart = (
  window: Window,
  granularity: Granularity,
  now: Date,
  earliest?: Date,
): Date => {
  if (window === "all") return startOfBucket(earliest ?? now, granularity);
  const days = WINDOW_DAYS[window];
  if (granularity === "hour")
    return addHours(startOfHour(now), -(days * 24 - 1));
  return startOfBucket(addDays(startOfDay(now), -(days - 1)), granularity);
};

export function claimsInWindow<T extends Dated>(
  items: T[],
  window: Window,
  granularity: Granularity,
  now: Date,
): T[] {
  if (window === "all") return items;
  const start = windowStart(window, granularity, now);
  return items.filter((item) => new Date(item.at) >= start);
}

export const earliestAt = (items: Dated[]): Date | undefined =>
  items.length
    ? new Date(Math.min(...items.map((item) => new Date(item.at).getTime())))
    : undefined;

export function buildBuckets<T extends Dated>(
  items: T[],
  window: Window,
  granularity: Granularity,
  now: Date,
  weightOf: (item: T) => number = () => 1,
  // Multi-series charts MUST pass one shared earliest (PR #23 review): the
  // "all" window otherwise starts each series at its own first item and the
  // chart's index-aligned buckets land in the wrong time bands.
  earliest: Date | undefined = undefined,
): Bucket[] {
  const dates = items.map((item) => new Date(item.at));
  const start = windowStart(
    window,
    granularity,
    now,
    earliest ?? earliestAt(items),
  );
  // Insertion order = chronological order; zero-fill first, then weigh.
  const counts = new Map<string, number>();
  for (
    let cursor = start;
    cursor <= now;
    cursor = advance(cursor, granularity)
  ) {
    counts.set(bucketKey(cursor, granularity), 0);
  }
  items.forEach((item, index) => {
    const date = dates[index];
    const key = bucketKey(date, granularity);
    if (date >= start && counts.has(key)) {
      counts.set(key, (counts.get(key) ?? 0) + weightOf(item));
    }
  });
  return [...counts.entries()].map(([key, count]) => ({ key, count }));
}
