import { useEffect, useState } from "react";

type Status = "loading" | "ok" | "error";

// Relative URL: Vite proxies "/api" to the backend in dev; SWA routes it in prod.
const HEALTH_URL = "/api/health";

export default function App() {
  const [status, setStatus] = useState<Status>("loading");

  useEffect(() => {
    let cancelled = false;
    fetch(HEALTH_URL)
      .then((res) => {
        if (!res.ok) throw new Error(`unexpected status ${res.status}`);
        return res.json();
      })
      .then((body: { status?: string }) => {
        if (!cancelled) setStatus(body.status === "ok" ? "ok" : "error");
      })
      .catch(() => {
        if (!cancelled) setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main>
      <h1>Claim Automation</h1>
      {status === "loading" && <p>Checking backend…</p>}
      {status === "ok" && <p>✅ All good</p>}
      {status === "error" && <p>⚠️ Backend unavailable</p>}
    </main>
  );
}
