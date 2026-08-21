import { useEffect, useState } from "react";

import { apiUrl } from "./api";
import { authFetch } from "./auth";

// The closed outcome set (mirrors backend HeartbeatStatus): the exact-key table
// makes a typo'd, stale, or missing label a compile error. The lookup stays
// permissive on purpose — an unknown value renders raw; degrade readable,
// never crash (REQ-4.5).
type RunOutcome =
  "ran" | "failed" | "skipped_disabled" | "skipped_no_access" | "skipped_busy";
const OUTCOME_LABELS: Record<RunOutcome, string> = {
  ran: "ran",
  failed: "failed",
  skipped_disabled: "skipped (worker off)",
  skipped_no_access: "skipped (Gmail needs reconnect)",
  skipped_busy: "skipped (another run in progress)",
};
const outcomeLabel = (outcome: string) =>
  OUTCOME_LABELS[outcome as RunOutcome] ?? outcome;

// processed/failed/failed_total: 5c run counts + failed-label gauge
// (pipeline-wiring REQ-10); matched: the 5b probe's legacy count. All optional
// so older backends and pre-5c heartbeat rows keep rendering.
type HeartbeatView = {
  at: string;
  status: string;
  matched?: number | null;
  processed?: number | null;
  failed?: number | null;
  failed_total?: number | null;
};

// The backend caps the gauge at one page (REQ-5): 101 means "more than 100".
const GAUGE_CAP = 100;

const lastRunText = (heartbeat: HeartbeatView) => {
  const base = `${new Date(heartbeat.at).toLocaleString()} — ${outcomeLabel(
    heartbeat.status,
  )}`;
  // != null, never truthiness: 0 is a successful, informative run (REQ-10).
  if (heartbeat.processed != null && heartbeat.failed != null) {
    const counts = `${base} — ${heartbeat.processed} processed, ${heartbeat.failed} failed`;
    if (heartbeat.failed_total == null) return counts;
    const gauge =
      heartbeat.failed_total > GAUGE_CAP
        ? `${GAUGE_CAP}+`
        : `${heartbeat.failed_total}`;
    return `${counts} · ${gauge} in failed state`;
  }
  if (heartbeat.matched == null) return base;
  const noun = heartbeat.matched === 1 ? "email" : "emails";
  return `${base} — ${heartbeat.matched} matching ${noun}`;
};
// Discriminated union per the structure.md data-fetching pattern: per-state
// data exists only in its state; render a distinct view for each.
type Panel =
  | { status: "loading" }
  | { status: "ok"; enabled: boolean; heartbeat: HeartbeatView | null }
  | { status: "error" };

const STATUS_URL = apiUrl("/api/worker/status");
const ENABLED_URL = apiUrl("/api/worker/enabled");
const RUN_URL = apiUrl("/api/worker/run");

function isHeartbeat(value: unknown): value is HeartbeatView {
  const heartbeat = value as HeartbeatView;
  return (
    typeof heartbeat === "object" &&
    heartbeat !== null &&
    typeof heartbeat.at === "string" &&
    typeof heartbeat.status === "string" &&
    // Deliberately optional (gate E8): requiring any count would flip the
    // card to the error state for older rows and backends.
    (heartbeat.matched == null || typeof heartbeat.matched === "number") &&
    (heartbeat.processed == null || typeof heartbeat.processed === "number") &&
    (heartbeat.failed == null || typeof heartbeat.failed === "number") &&
    (heartbeat.failed_total == null ||
      typeof heartbeat.failed_total === "number")
  );
}

export default function WorkerControls() {
  const [panel, setPanel] = useState<Panel>({ status: "loading" });
  // One flag disables both controls while any POST is in flight (single operator).
  const [busy, setBusy] = useState(false);
  const [runOutcome, setRunOutcome] = useState<string | null>(null);
  const [actionFailed, setActionFailed] = useState(false);
  // Bumped after every action: never assume a write landed — re-read (REQ-4.3/4.4).
  const [statusEpoch, setStatusEpoch] = useState(0);

  useEffect(() => {
    let cancelled = false;
    authFetch(STATUS_URL)
      .then((res) => {
        if (!res.ok) throw new Error(`unexpected status ${res.status}`);
        return res.json();
      })
      .then((body: { enabled?: unknown; heartbeat?: unknown }) => {
        if (cancelled) return;
        // A 200 with a broken contract is an error state, not success.
        if (
          typeof body.enabled === "boolean" &&
          (body.heartbeat === null || isHeartbeat(body.heartbeat))
        ) {
          setPanel({
            status: "ok",
            enabled: body.enabled,
            heartbeat: body.heartbeat as HeartbeatView | null,
          });
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
  }, [statusEpoch]);

  const refreshStatus = () => setStatusEpoch((epoch) => epoch + 1);

  const post = async (url: string, body?: unknown): Promise<unknown> => {
    const res = await authFetch(url, {
      method: "POST",
      ...(body !== undefined && {
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    });
    if (!res.ok) throw new Error(`unexpected status ${res.status}`);
    return res.json();
  };

  const toggle = async (target: boolean) => {
    setBusy(true);
    setActionFailed(false);
    // A lingering run result would describe a run under the OLD enabled state (L2).
    setRunOutcome(null);
    try {
      const body = (await post(ENABLED_URL, { enabled: target })) as {
        enabled?: unknown;
      };
      if (typeof body.enabled !== "boolean") throw new Error("broken contract");
      setPanel((current) =>
        current.status === "ok"
          ? { ...current, enabled: body.enabled as boolean }
          : current,
      );
    } catch {
      setActionFailed(true);
      refreshStatus();
    } finally {
      setBusy(false);
    }
  };

  const processNow = async () => {
    setBusy(true);
    setActionFailed(false);
    setRunOutcome(null);
    try {
      const body = (await post(RUN_URL)) as { outcome?: unknown };
      if (typeof body.outcome !== "string") throw new Error("broken contract");
      setRunOutcome(body.outcome);
    } catch {
      // The heartbeat row carries the truth (e.g. `failed`) — surface it via
      // the refresh below.
      setActionFailed(true);
    } finally {
      refreshStatus();
      setBusy(false);
    }
  };

  // <article> renders as a Pico card; the switch is a native checkbox because
  // Pico's switch styling targets [type=checkbox][role=switch]. Its accessible
  // name IS the visible label text (WCAG 2.5.3 label-in-name, review L1 —
  // voice control must be able to target what sighted users read).
  if (panel.status === "loading") {
    return (
      <article>
        <h2>Worker</h2>
        <p aria-busy="true">Checking worker…</p>
      </article>
    );
  }
  if (panel.status === "error") {
    return (
      <article>
        <h2>Worker</h2>
        <p role="alert">⚠️ Worker status unavailable</p>
      </article>
    );
  }
  return (
    <article>
      <h2>Worker</h2>
      <label>
        <input
          type="checkbox"
          role="switch"
          checked={panel.enabled}
          onChange={() => toggle(!panel.enabled)}
          disabled={busy}
        />
        Worker enabled
      </label>
      <p>
        Last run: {panel.heartbeat ? lastRunText(panel.heartbeat) : "never"}
      </p>
      <p>
        <button onClick={processNow} disabled={busy} aria-busy={busy}>
          Process now
        </button>
        {runOutcome !== null && (
          <span> Run result: {outcomeLabel(runOutcome)}</span>
        )}
      </p>
      {actionFailed && <p role="alert">⚠️ Action failed — status refreshed</p>}
    </article>
  );
}
