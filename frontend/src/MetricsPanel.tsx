import { useEffect, useState } from "react";

import { apiUrl } from "./api";
import { authFetch } from "./auth";
import ClaimsChart from "./ClaimsChart";
import {
  buildBuckets,
  earliestAt,
  claimsInWindow,
  defaultGranularity,
  GRANULARITY_MATRIX,
  WINDOWS,
  type Granularity,
  type Window,
} from "./metrics";
import {
  GRANULARITY_LABEL,
  GRANULARITY_LABELS,
  LOADING_METRICS,
  METRICS_TITLE,
  METRICS_UNAVAILABLE,
  NO_CLAIMS_IN_WINDOW,
  SERIES_CLAIMS,
  SERIES_ERRORS,
  TABLE_CLAIM,
  TABLE_DATE,
  TABLE_OWNER,
  TABLE_TOWN,
  TABLE_TYPE,
  TILE_CARDS_CREATED,
  TILE_EMAILS_FAILED,
  TILE_EMAILS_PROCESSED,
  TILE_FAILED_RUNS,
  TIME_WINDOW_LABEL,
  WINDOW_LABELS,
} from "./strings";

// metrics-dashboard REQ-2/3/4 + delta REQ-8/9/10: one fetch on mount carries
// everything; the window × granularity controls re-derive the chart and list
// locally. Mount-only by design (accepted staleness — spec Design): a reload
// shows post-mount activity.

// town/owner tolerate absent keys as well as null (Gate 3 W5): a backend that
// starts excluding None properties must not flip the whole panel to error.
type ClaimView = {
  at: string;
  claim_ref: string;
  type: string;
  town?: string | null;
  owner?: string | null;
  card_url: string;
};

type ErrorRunView = { at: string; failed: number };

type Metrics = {
  emails_processed: number;
  cards_created: number;
  emails_failed: number;
  failed_runs: number;
  error_runs: ErrorRunView[];
  claims: ClaimView[];
};

// Discriminated union per the structure.md data-fetching pattern.
type Panel =
  | { status: "loading" }
  | { status: "ok"; metrics: Metrics }
  | { status: "error" };

const METRICS_URL = apiUrl("/api/metrics");
const DEFAULT_WINDOW: Window = "30d";
// WINDOW_LABELS/GRANULARITY_LABELS moved to strings.ts (frontend-spanish REQ-1.3).
// Series colors: claims ride Pico's theme primary; the error hue is fixed and
// validated with the dataviz palette script against BOTH Pico surfaces/modes
// (delta D4 — Pico's own dark primary sits outside the validator's lightness
// band, an accepted design-system deviation; all pair checks pass).
const CLAIMS_COLOR = "var(--pico-primary)";
const ERRORS_COLOR = "#d97706";

const isDate = (value: unknown): value is string =>
  typeof value === "string" && !Number.isNaN(Date.parse(value));

function isClaim(value: unknown): value is ClaimView {
  const claim = value as ClaimView;
  return (
    typeof claim === "object" &&
    claim !== null &&
    // An unparseable date would silently blank the chart while the table
    // still lists the claim (Gate 3 C1) — broken contract, fail loud.
    isDate(claim.at) &&
    typeof claim.claim_ref === "string" &&
    typeof claim.type === "string" &&
    typeof claim.card_url === "string" &&
    (claim.town == null || typeof claim.town === "string") &&
    (claim.owner == null || typeof claim.owner === "string")
  );
}

function isErrorRun(value: unknown): value is ErrorRunView {
  const run = value as ErrorRunView;
  return (
    typeof run === "object" &&
    run !== null &&
    isDate(run.at) &&
    typeof run.failed === "number"
  );
}

function isMetrics(value: unknown): value is Metrics {
  const metrics = value as Metrics;
  return (
    typeof metrics === "object" &&
    metrics !== null &&
    typeof metrics.emails_processed === "number" &&
    typeof metrics.cards_created === "number" &&
    typeof metrics.emails_failed === "number" &&
    typeof metrics.failed_runs === "number" &&
    Array.isArray(metrics.error_runs) &&
    metrics.error_runs.every(isErrorRun) &&
    Array.isArray(metrics.claims) &&
    metrics.claims.every(isClaim)
  );
}

export default function MetricsPanel() {
  const [panel, setPanel] = useState<Panel>({ status: "loading" });
  const [window_, setWindow] = useState<Window>(DEFAULT_WINDOW);
  const [granularity, setGranularity] = useState<Granularity>(
    defaultGranularity(DEFAULT_WINDOW),
  );

  useEffect(() => {
    let cancelled = false;
    authFetch(METRICS_URL)
      .then((res) => {
        if (!res.ok) throw new Error(`unexpected status ${res.status}`);
        return res.json();
      })
      .then((body: unknown) => {
        if (cancelled) return;
        // A 200 with a broken contract is an error state, not success.
        if (isMetrics(body)) {
          setPanel({ status: "ok", metrics: body });
        } else {
          setPanel({ status: "error" });
        }
      })
      .catch(() => {
        if (!cancelled) setPanel({ status: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const changeWindow = (next: Window) => {
    setWindow(next);
    // Snap to the new window's default — the old granularity may be invalid.
    setGranularity(defaultGranularity(next));
  };

  if (panel.status === "loading") {
    return (
      <article>
        <h2>{METRICS_TITLE}</h2>
        <p aria-busy="true">{LOADING_METRICS}</p>
      </article>
    );
  }
  if (panel.status === "error") {
    return (
      <article>
        <h2>{METRICS_TITLE}</h2>
        <p role="alert">⚠️ {METRICS_UNAVAILABLE}</p>
      </article>
    );
  }
  const { metrics } = panel;
  const now = new Date();
  const claims = claimsInWindow(metrics.claims, window_, granularity, now);
  // One timeline for BOTH chart series (PR #23 review fix): the shared
  // earliest keeps the "Todo" window's bucket arrays index-aligned.
  const sharedEarliest = earliestAt([...metrics.claims, ...metrics.error_runs]);
  return (
    <article>
      <h2>{METRICS_TITLE}</h2>
      <div className="grid">
        <p>
          {TILE_EMAILS_PROCESSED}
          <br />
          <strong>{metrics.emails_processed}</strong>
        </p>
        <p>
          {TILE_CARDS_CREATED}
          <br />
          <strong>{metrics.cards_created}</strong>
        </p>
        <p>
          {TILE_EMAILS_FAILED}
          <br />
          <strong>{metrics.emails_failed}</strong>
        </p>
        <p>
          {TILE_FAILED_RUNS}
          <br />
          <strong>{metrics.failed_runs}</strong>
        </p>
      </div>
      <div className="grid">
        <label>
          {TIME_WINDOW_LABEL}
          <select
            value={window_}
            onChange={(event) => changeWindow(event.target.value as Window)}
          >
            {WINDOWS.map((value) => (
              <option key={value} value={value}>
                {WINDOW_LABELS[value]}
              </option>
            ))}
          </select>
        </label>
        <label>
          {GRANULARITY_LABEL}
          <select
            value={granularity}
            onChange={(event) =>
              setGranularity(event.target.value as Granularity)
            }
          >
            {GRANULARITY_MATRIX[window_].map((value) => (
              <option key={value} value={value}>
                {GRANULARITY_LABELS[value]}
              </option>
            ))}
          </select>
        </label>
      </div>
      <ClaimsChart
        series={[
          {
            name: SERIES_CLAIMS,
            color: CLAIMS_COLOR,
            buckets: buildBuckets(
              metrics.claims,
              window_,
              granularity,
              now,
              () => 1,
              sharedEarliest,
            ),
          },
          {
            name: SERIES_ERRORS,
            color: ERRORS_COLOR,
            buckets: buildBuckets(
              metrics.error_runs,
              window_,
              granularity,
              now,
              (run) => run.failed,
              sharedEarliest,
            ),
          },
        ]}
      />
      {claims.length === 0 ? (
        <p>{NO_CLAIMS_IN_WINDOW}</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>{TABLE_DATE}</th>
              <th>{TABLE_CLAIM}</th>
              <th>{TABLE_TYPE}</th>
              <th>{TABLE_TOWN}</th>
              <th>{TABLE_OWNER}</th>
            </tr>
          </thead>
          <tbody>
            {claims.map((claim) => (
              <tr key={claim.claim_ref}>
                <td>{new Date(claim.at).toLocaleString()}</td>
                <td>
                  {/* noopener/noreferrer: external target must not reach back. */}
                  <a
                    href={claim.card_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {claim.claim_ref}
                  </a>
                </td>
                <td>{claim.type}</td>
                <td>{claim.town ?? "—"}</td>
                <td>{claim.owner ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </article>
  );
}
