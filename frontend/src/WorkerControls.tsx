import { useEffect, useState } from "react";

import { apiUrl } from "./api";
import { authFetch } from "./auth";

// The closed outcome set lives in one place; an unknown value (item 5 adds
// skipped_no_access) renders raw — degrade readable, never crash (REQ-4.5).
const OUTCOME_LABELS: Record<string, string> = {
  ran: "ran",
  failed: "failed",
  skipped_disabled: "skipped (worker off)",
};
const outcomeLabel = (outcome: string) => OUTCOME_LABELS[outcome] ?? outcome;

type HeartbeatView = { at: string; status: string };
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
    typeof heartbeat.status === "string"
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
  // name stays "Worker enabled" (aria-label wins over the On/Off label text).
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
          aria-label="Worker enabled"
          checked={panel.enabled}
          onChange={() => toggle(!panel.enabled)}
          disabled={busy}
        />
        {panel.enabled ? "On" : "Off"}
      </label>
      <p>
        Last run:{" "}
        {panel.heartbeat
          ? `${new Date(panel.heartbeat.at).toLocaleString()} — ${outcomeLabel(
              panel.heartbeat.status,
            )}`
          : "never"}
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
