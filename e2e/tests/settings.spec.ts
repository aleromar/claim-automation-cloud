import { expect, test } from "@playwright/test";

import { OPERATOR } from "./constants";
import { login, restoreSecret, storedSecret } from "./helpers";

// Settings tab cross-stack flow (settings spec REQ-4): real backend, Azurite
// tables, file secret store. Serial: shares the backend's secret file.
test.describe.configure({ mode: "serial" });

// The save below writes a fake trello-api-key into the shared dev store, and
// the UI is deliberately unable to clear a credential — restore what was
// there before (worker.spec discipline: leave shared dev state as found).
let trelloKeyBefore: string | undefined;
test.beforeAll(() => {
  trelloKeyBefore = storedSecret("trello-api-key");
});
test.afterAll(() => {
  restoreSecret("trello-api-key", trelloKeyBefore);
});

test("save Trello settings, persist across reload, secrets write-only", async ({ page }) => {
  await login(page);
  await page.getByRole("link", { name: "Settings" }).click();
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();

  // Unique per run: the unprefixed Azurite tables are shared mutable dev state.
  const boardId = `board-${Date.now()}`;
  const listId = `list-${Date.now()}`;
  await page.getByLabel(/board id/i).fill(boardId);
  await page.getByLabel(/list id/i).fill(listId);
  await page.getByLabel(/api key/i).fill("e2e-trello-key");
  await page.getByRole("button", { name: /save trello settings/i }).click();

  // Response re-render: badge flips to stored, input returns to empty (write-only).
  await expect(page.getByLabel(/api key \(stored\)/i)).toHaveValue("");
  expect(storedSecret("trello-api-key")).toBe("e2e-trello-key");

  // Reload = fresh GET /api/settings AND the dev-server SPA fallback for /settings.
  await page.reload();
  await expect(page.getByLabel(/board id/i)).toHaveValue(boardId);
  await expect(page.getByLabel(/list id/i)).toHaveValue(listId);
  await expect(page.getByLabel(/api key \(stored\)/i)).toHaveValue("");
});

test("reconnect Gmail re-consents and replaces the stored refresh token (REQ-3)", async ({
  page,
}) => {
  await login(page);
  const tokenBefore = storedSecret("gmail-refresh-token");
  expect(tokenBefore).toBeTruthy();

  await page.getByRole("link", { name: "Settings" }).click();
  await expect(page.getByText(/refresh token: stored/i)).toBeVisible();
  // Top-level navigation through the Vite proxy → backend 302 → stub consent.
  await page.getByRole("button", { name: /reconnect gmail/i }).click();
  await page.getByRole("link", { name: "Approve", exact: true }).click();

  // Callback returns to the SPA root with a fresh session; new token stored.
  await expect(page.getByText(OPERATOR)).toBeVisible();
  expect(storedSecret("gmail-refresh-token")).toBeTruthy();
  expect(storedSecret("gmail-refresh-token")).not.toBe(tokenBefore);
});
