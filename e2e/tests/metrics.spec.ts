import { expect, test } from "@playwright/test";

import { login } from "./helpers";

// Metrics panel smoke (metrics-dashboard REQ-6): real backend + Azurite.
// Numeric-shape assertions only, never exact zeros — local dev tables are
// shared with `make dev` runs (same stance as worker.spec.ts).

test("dashboard shows the metrics panel and no reconnect banner", async ({
  page,
}) => {
  await login(page);

  // Tiles render numeric all-time counters. FULL labels on purpose (delta
  // gate E8): a loose /fallidos/i would match two tiles (strict mode).
  await expect(page.getByText(/correos procesados/i)).toHaveText(/\d+/);
  await expect(page.getByText(/tarjetas creadas/i)).toHaveText(/\d+/);
  await expect(page.getByText(/correos fallidos/i)).toHaveText(/\d+/);
  await expect(page.getByText(/ejecuciones fallidas/i)).toHaveText(/\d+/);

  // Window + granularity controls and the two-series chart present.
  await expect(
    page.getByRole("combobox", { name: /periodo/i }),
  ).toBeVisible();
  await expect(
    page.getByRole("combobox", { name: /granularidad/i }),
  ).toBeVisible();
  await expect(
    page.getByRole("img", { name: /siniestros y errores por intervalo/i }),
  ).toBeVisible();

  // The list region resolves to rows or the explicit empty message.
  await expect(
    page.getByText(/no hay siniestros en este periodo/i).or(page.locator("table tbody tr").first()),
  ).toBeVisible();

  // Healthy token state: no reconnect banner. toHaveCount(0) passes on a
  // not-yet-fetched banner too (Gate 3 W1) — settle all in-flight requests
  // first so the assertion means "fetched and hidden", not "not fetched yet".
  // Legit local red: a dev-shared Azurite heartbeat left at skipped_no_access
  // (same shared-tables stance as worker.spec.ts; CI's fresh Azurite is clean).
  await page.waitForLoadState("networkidle");
  await expect(
    page.getByRole("link", { name: /reconectar gmail/i }),
  ).toHaveCount(0);
});
