// TZ is pinned via vite.config.ts `test.env` (set before the worker boots —
// an assignment here would run after hoisted imports; metrics-dashboard
// gates M3/W6). Bucket tests rely on it: Madrid = the operator's clock.
import "@testing-library/jest-dom";
import { afterEach, vi } from "vitest";

// Shared teardown: token/session state and URL are process-global (jsdom).
afterEach(() => {
  sessionStorage.clear();
  window.location.hash = "";
  history.replaceState(null, "", "/");
  vi.restoreAllMocks();
});
