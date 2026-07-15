import { useEffect, useState } from "react";

import { authFetch, getToken } from "./auth";
import Login from "./Login";

type Session = "anonymous" | "checking" | "authed";
type Health = "loading" | "ok" | "error";

// Relative URL: Vite proxies "/api" to the backend in dev; SWA routes it in prod.
const HEALTH_URL = "/api/health";

export default function App({
  initialError = null,
}: {
  initialError?: string | null;
}) {
  const [session, setSession] = useState<Session>(() =>
    getToken() ? "checking" : "anonymous",
  );
  const [email, setEmail] = useState("");
  const [health, setHealth] = useState<Health>("loading");

  useEffect(() => {
    if (session !== "checking") return;
    let cancelled = false;
    authFetch("/api/me")
      .then((res) => {
        if (!res.ok) throw new Error(`unexpected status ${res.status}`);
        return res.json();
      })
      .then((body: { email?: string }) => {
        if (cancelled) return;
        if (body.email) {
          setEmail(body.email);
          setSession("authed");
        } else {
          setSession("anonymous");
        }
      })
      .catch(() => {
        if (!cancelled) setSession("anonymous");
      });
    return () => {
      cancelled = true;
    };
  }, [session]);

  useEffect(() => {
    if (session !== "authed") return;
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

  if (session === "anonymous") return <Login error={initialError} />;
  if (session === "checking") {
    return (
      <main>
        <h1>Claim Automation</h1>
        <p>Checking session…</p>
      </main>
    );
  }
  return (
    <main>
      <h1>Claim Automation</h1>
      <p>{email}</p>
      {health === "loading" && <p>Checking backend…</p>}
      {health === "ok" && <p>✅ All good</p>}
      {health === "error" && <p>⚠️ Backend unavailable</p>}
    </main>
  );
}
