import "@testing-library/jest-dom";
import { afterEach, vi } from "vitest";

// Shared teardown: token/session state and URL are process-global (jsdom).
afterEach(() => {
  sessionStorage.clear();
  window.location.hash = "";
  history.replaceState(null, "", "/");
  vi.restoreAllMocks();
});
