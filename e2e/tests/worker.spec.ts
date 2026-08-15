import { expect, test } from "@playwright/test";

// Worker controls panel (worker-controls REQ-4): real backend + Azurite.
// The test drives itself to a deterministic state through the UI — the
// explicit-target toggle is idempotent — and always leaves enabled=false
// (local dev tables are shared; OFF is the fail-safe).
const OPERATOR = "operator@example.com";

test("operator sets the worker off and process-now reports skipped", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("link", { name: /sign in with google/i }).click();
  await page.getByRole("link", { name: "Approve", exact: true }).click();
  await expect(page.getByText(OPERATOR)).toBeVisible();

  const workerSwitch = page.getByRole("switch", { name: /worker enabled/i });
  await expect(workerSwitch).toBeVisible();
  if ((await workerSwitch.getAttribute("aria-checked")) === "true") {
    await workerSwitch.click();
  }
  await expect(workerSwitch).toHaveAttribute("aria-checked", "false");

  await page.getByRole("button", { name: /process now/i }).click();
  await expect(page.getByText(/run result:/i)).toHaveText(
    /skipped \(worker off\)/i,
  );
  // The wake wrote its end-of-run heartbeat: last-run now shows a timestamp.
  await expect(page.getByText(/last run:/i)).not.toHaveText(/never/i);
});
