import { useEffect, useState } from "react";

import { apiUrl } from "./api";
import { authFetch, clearToken, getToken } from "./auth";
import Login from "./Login";
import WorkerControls from "./WorkerControls";
import {
  buildVersion,
  MISSING_VERSION,
  shortSha,
  UNKNOWN_VERSION,
} from "./version";

// Discriminated union: the email exists only in the authed state.
type Session =
  | { status: "anonymous" }
  | { status: "checking" }
  | { status: "authed"; email: string }
  | { status: "error"; message: string };
// Same pattern: the backend version exists only in the ok state (version-display REQ-3).
// `version` holds the display form (short SHA or a version.ts sentinel) —
// shortening happens at the fetch boundary, where the value is known to be a SHA.
type Health =
  | { status: "loading" }
  | { status: "ok"; version: string }
  | { status: "error" };

// apiUrl: relative via the Vite proxy in dev, absolute Function App origin in prod (REQ-4.2).
const HEALTH_URL = apiUrl("/api/health");

export default function App({
  initialError = null,
}: {
  initialError?: string | null;
}) {
  const [session, setSession] = useState<Session>(() =>
    getToken() ? { status: "checking" } : { status: "anonymous" },
  );
  const [health, setHealth] = useState<Health>({ status: "loading" });

  useEffect(() => {
    if (session.status !== "checking") return;
    let cancelled = false;
    authFetch(apiUrl("/api/me"))
      .then((res) => {
        if (!res.ok) throw new Error(`unexpected status ${res.status}`);
        return res.json();
      })
      .then((body: { email?: string }) => {
        if (cancelled) return;
        if (body.email) {
          setSession({ status: "authed", email: body.email });
        } else {
          // 200 without an email is a broken contract — surface it, don't
          // silently bounce the operator back to the login screen.
          setSession({
            status: "error",
            message:
              "Session check returned an unexpected response (no account email).",
          });
        }
      })
      .catch(() => {
        if (!cancelled) setSession({ status: "anonymous" });
      });
    return () => {
      cancelled = true;
    };
  }, [session]);

  useEffect(() => {
    if (session.status !== "authed") return;
    let cancelled = false;
    authFetch(HEALTH_URL)
      .then((res) => {
        if (!res.ok) throw new Error(`unexpected status ${res.status}`);
        return res.json();
      })
      .then((body: { status?: string; version?: string }) => {
        if (cancelled) return;
        if (body.status === "ok") {
          // Ok without a version field stays ok (the contract is keyed on
          // `status`; `version` is additive) — shown as "unknown" (REQ-3.3).
          setHealth({
            status: "ok",
            version: body.version ? shortSha(body.version) : UNKNOWN_VERSION,
          });
        } else {
          setHealth({ status: "error" });
        }
      })
      .catch(() => {
        if (!cancelled) setHealth({ status: "error" });
      });
    return () => {
      cancelled = true;
    };
    // Keep deps as [session] only: `health` is a fresh object per fetch —
    // adding it here would loop the effect.
  }, [session]);

  if (session.status === "anonymous") return <Login error={initialError} />;
  if (session.status === "checking") {
    return (
      <main className="container">
        <h1>Claim Automation</h1>
        <p aria-busy="true">Checking session…</p>
      </main>
    );
  }
  if (session.status === "error") {
    return (
      <main className="container">
        <h1>Claim Automation</h1>
        <p role="alert">⚠️ {session.message}</p>
      </main>
    );
  }
  // REQ-4.5/4.6 (logout delta): discards the browser's JWT only — no server-side
  // revocation, and the stored Gmail refresh token is untouched.
  const logout = () => {
    clearToken();
    setSession({ status: "anonymous" });
  };

  return (
    <>
      {/* Pico classless: nav bar in a header, content sections in main; the
          only classes are Pico's own container/secondary. */}
      <header className="container">
        <nav>
          <ul>
            <li>
              <strong>Claim Automation</strong>
            </li>
          </ul>
          <ul>
            <li>{session.email}</li>
            <li>
              <button className="secondary" onClick={logout}>
                Log out
              </button>
            </li>
          </ul>
        </nav>
      </header>
      <main className="container">
        {health.status === "loading" && (
          <p aria-busy="true">Checking backend…</p>
        )}
        {health.status === "ok" && <p>✅ All good</p>}
        {health.status === "error" && <p>⚠️ Backend unavailable</p>}
        <WorkerControls />
      </main>
      {/* Outside <main> so it carries the contentinfo landmark role. Short SHAs;
          a mismatch is normal — deploys are path-filtered (version-display V4). */}
      <footer className="container">
        <small>
          backend {health.status === "ok" ? health.version : MISSING_VERSION} ·
          frontend {shortSha(buildVersion())}
        </small>
      </footer>
    </>
  );
}
