import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { apiUrl } from "./api";
import { authFetch } from "./auth";
import {
  RECONNECT_BANNER_TEXT,
  RECONNECT_GMAIL,
  SKIPPED_NO_ACCESS,
} from "./strings";

// metrics-dashboard REQ-5: latest-heartbeat rule. Renders nothing unless the
// last run was skipped_no_access — absence is the healthy default, and
// WorkerControls owns the visible loading/error states for worker status.
// Own mount-only fetch by design (spec: two point-read GETs per dashboard
// load beat refactoring a tested component's epoch-driven state).

const STATUS_URL = apiUrl("/api/worker/status");

export default function ReconnectBanner() {
  const [stale, setStale] = useState(false);

  useEffect(() => {
    let cancelled = false;
    authFetch(STATUS_URL)
      .then((res) => {
        if (!res.ok) throw new Error(`unexpected status ${res.status}`);
        return res.json();
      })
      .then((body: { heartbeat?: { status?: unknown } | null }) => {
        if (cancelled) return;
        setStale(body.heartbeat?.status === SKIPPED_NO_ACCESS);
      })
      .catch(() => {
        // Stay hidden: a status outage already surfaces in WorkerControls.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!stale) return null;
  return (
    <p role="alert">
      ⚠️ {RECONNECT_BANNER_TEXT} <Link to="/settings">{RECONNECT_GMAIL}</Link>
    </p>
  );
}
