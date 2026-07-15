import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { consumeFragment } from "./auth";

// Consume #token=…/#error=… BEFORE first render (REQ-4.1) — the token must not
// survive in the URL or browser history.
const { error } = consumeFragment();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App initialError={error} />
  </StrictMode>,
);
