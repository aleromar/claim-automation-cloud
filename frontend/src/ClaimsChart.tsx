import type { Bucket } from "./metrics";
import { CHART_ARIA_LABEL } from "./strings";

// Multi-series grouped-bar SVG chart (delta REQ-9.2; dataviz method): bars
// ≤24px with a 4px rounded data-end and square baseline, hairline recessive
// axes, per-bucket <title> tooltips on a full-band hit target, legend only
// for ≥2 series (a single series is named by the panel heading). Marks wear
// series colors; text wears the muted text token, never the data color.
// Slot widths clamp ≥1px and the inter-series gap collapses on dense windows
// (168 hourly buckets) — a width can never go negative (delta gate E2).

export type ChartSeries = { name: string; color: string; buckets: Bucket[] };

const WIDTH = 600;
const HEIGHT = 160;
const PAD_LEFT = 28;
const PAD_RIGHT = 4;
const PAD_TOP = 12;
const PAD_BOTTOM = 18;
const PLOT_W = WIDTH - PAD_LEFT - PAD_RIGHT;
const PLOT_H = HEIGHT - PAD_TOP - PAD_BOTTOM;
const BASELINE = PAD_TOP + PLOT_H;
const MAX_BAR_W = 24;
const BAR_GAP = 2;
const CAP_RADIUS = 4;

// Rounded top, square baseline (a plain rect rx would round all four corners).
const barPath = (x: number, width: number, height: number): string => {
  const r = Math.min(CAP_RADIUS, width / 2, height);
  const top = BASELINE - height;
  return [
    `M ${x} ${BASELINE}`,
    `V ${top + r}`,
    `Q ${x} ${top} ${x + r} ${top}`,
    `H ${x + width - r}`,
    `Q ${x + width} ${top} ${x + width} ${top + r}`,
    `V ${BASELINE}`,
    "Z",
  ].join(" ");
};

export default function ClaimsChart({ series }: { series: ChartSeries[] }) {
  const buckets = series[0]?.buckets ?? [];
  // Shared y-max across all series, clamped ≥ 1 (REQ-3.4): an all-zero
  // window must not divide by zero into NaN.
  const yMax = Math.max(
    1,
    ...series.flatMap((s) => s.buckets.map((b) => b.count)),
  );
  const band = PLOT_W / Math.max(1, buckets.length);
  const slot = band / Math.max(1, series.length);
  // Dense windows: the gap would exceed the slot — collapse it (gate E2).
  const gap = band < 4 ? 0 : BAR_GAP;
  const barWidth = Math.max(1, Math.min(MAX_BAR_W, slot - gap));
  const first = buckets[0];
  const last = buckets.length > 1 ? buckets[buckets.length - 1] : undefined;
  return (
    <figure>
      {series.length > 1 && (
        <p>
          {series.map((s) => (
            <small key={s.name} style={{ marginRight: "1rem" }}>
              <span aria-hidden="true" style={{ color: s.color }}>
                ■
              </span>{" "}
              {s.name}
            </small>
          ))}
        </p>
      )}
      <svg
        role="img"
        aria-label={CHART_ARIA_LABEL}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        style={{ width: "100%", height: "auto" }}
      >
        {buckets.map((bucket, index) => {
          const bandStart = PAD_LEFT + index * band;
          const values = series.map(
            (s) => `${s.name}: ${s.buckets[index]?.count ?? 0}`,
          );
          return (
            <g key={bucket.key}>
              <title>{`${bucket.key} — ${values.join(", ")}`}</title>
              {/* Hit target: the whole band, taller than the marks. */}
              <rect
                x={bandStart}
                y={PAD_TOP}
                width={band}
                height={PLOT_H}
                fill="transparent"
              />
              {series.map((s, seriesIndex) => {
                const count = s.buckets[index]?.count ?? 0;
                if (count <= 0) return null;
                const slotStart = bandStart + seriesIndex * slot;
                return (
                  <path
                    key={s.name}
                    d={barPath(
                      slotStart + (slot - barWidth) / 2,
                      barWidth,
                      (count / yMax) * PLOT_H,
                    )}
                    fill={s.color}
                  />
                );
              })}
            </g>
          );
        })}
        <line
          x1={PAD_LEFT}
          y1={BASELINE}
          x2={WIDTH - PAD_RIGHT}
          y2={BASELINE}
          stroke="var(--pico-muted-border-color)"
          strokeWidth="1"
        />
        <text
          x={PAD_LEFT - 4}
          y={PAD_TOP + 4}
          textAnchor="end"
          fontSize="10"
          fill="var(--pico-muted-color)"
        >
          {yMax}
        </text>
        {/* Window edges only — a label per bucket would collide. */}
        {first && (
          <text
            x={PAD_LEFT}
            y={HEIGHT - 4}
            fontSize="10"
            fill="var(--pico-muted-color)"
          >
            {first.key}
          </text>
        )}
        {last && (
          <text
            x={WIDTH - PAD_RIGHT}
            y={HEIGHT - 4}
            textAnchor="end"
            fontSize="10"
            fill="var(--pico-muted-color)"
          >
            {last.key}
          </text>
        )}
      </svg>
    </figure>
  );
}
