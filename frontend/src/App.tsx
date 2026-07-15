import { useEffect, useState } from "react";

import { apiUrl } from "./api";
import { authFetch, getToken } from "./auth";
import Login from "./Login";

// Discriminated union: the email exists only in the authed state.
type Session =
  | { status: "anonymous" }
  | { status: "checking" }
  | { status: "authed"; email: string }
  | { status: "error"; message: string };
type Health = "loading" | "ok" | "error";

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
  const [health, setHealth] = useState<Health>("loading");

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
      .then((body: { status?: string }) => {
        if (!cancelled) setHealth(body.status === "ok" ? "ok" : "error");
      })
      .catch(() => {
        if (!cancelled) setHealth("error");
      });
    return () => {
      cancelled = true;
    };
  }, [session]);

  if (session.status === "anonymous") return <Login error={initialError} />;
  if (session.status === "checking") {
    return (
      <main>
        <h1>Claim Automation</h1>
        <p>Checking session…</p>
      </main>
    );
  }
  if (session.status === "error") {
    return (
      <main>
        <h1>Claim Automation</h1>
        <p role="alert">⚠️ {session.message}</p>
      </main>
    );
  }
  return (
    <main>
      <h1>Claim Automation</h1>
      <p>{session.email}</p>
      {health === "loading" && <p>Checking backend…</p>}
      {health === "ok" && <p>✅ All good</p>}
      {health === "error" && <p>⚠️ Backend unavailable</p>}
    </main>
  );
}
