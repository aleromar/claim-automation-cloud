import { expect, test } from "@playwright/test";

import { login } from "./helpers";

// Worker controls panel (worker-controls REQ-4): real backend + Azurite.
// The test drives itself to a deterministic state through the UI — the
// explicit-target toggle is idempotent — and always leaves enabled=false
// (local dev tables are shared; OFF is the fail-safe).

test("operator sets the worker off and process-now reports skipped", async ({
  page,
}) => {
  await login(page);

  const workerSwitch = page.getByRole("switch", { name: /proceso activado/i });
  await expect(workerSwitch).toBeVisible();
  if (await workerSwitch.isChecked()) {
    await workerSwitch.click();
  }
  await expect(workerSwitch).not.toBeChecked();

  await page.getByRole("button", { name: /procesar ahora/i }).click();
  await expect(page.getByText(/resultado:/i)).toHaveText(
    /omitido \(proceso desactivado\)/i,
  );
  // The wake wrote its end-of-run heartbeat: last-run now shows a timestamp.
  await expect(page.getByText(/última ejecución:/i)).not.toHaveText(/nunca/i);
});
