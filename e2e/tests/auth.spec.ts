import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

// Full cross-stack login flow against the stub IdP (auth spec REQ-6).
// Serial: these tests share the backend's file secret store.
test.describe.configure({ mode: "serial" });

const SECRETS_PATH = fileURLToPath(new URL("../.tmp/secrets.json", import.meta.url));
const OPERATOR = "operator@example.com";

const storedRefreshToken = () =>
  JSON.parse(readFileSync(SECRETS_PATH, "utf8"))["gmail-refresh-token"];

test("operator signs in via Google (stub) and sees the dashboard", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: /sign in with google/i }).click();
  await page.getByRole("link", { name: "Approve", exact: true }).click();

  await expect(page.getByText(OPERATOR)).toBeVisible();
  await expect(page.getByText(/all good/i)).toBeVisible();
  expect(page.url()).not.toContain("token="); // fragment stripped (REQ-4.1)
  expect(storedRefreshToken()).toBeTruthy(); // broker stored it (REQ-2.2)

  // Logout (REQ-4.5/4.6): await the login-screen render — the JWT is cleared
  // synchronously in the click handler; token-gone is asserted at the Vitest layer.
  await page.getByRole("button", { name: /log out/i }).click();
  await expect(page.getByRole("link", { name: /sign in with google/i })).toBeVisible();

  // Round trip: logging back in after logout re-enters the dashboard cleanly.
  await page.getByRole("link", { name: /sign in with google/i }).click();
  await page.getByRole("link", { name: "Approve", exact: true }).click();
  await expect(page.getByText(OPERATOR)).toBeVisible();
});

test("cancelling at the consent screen returns to login with a generic error", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("link", { name: /sign in with google/i }).click();
  await page.getByRole("link", { name: "Deny", exact: true }).click();

  await expect(page.getByRole("alert")).toHaveText(/login failed/i);
  await expect(page.getByRole("link", { name: /sign in with google/i })).toBeVisible();
});

test("repeat login without a refresh token keeps the stored one (REQ-2.3)", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: /sign in with google/i }).click();
  await page.getByRole("link", { name: "Approve", exact: true }).click();
  await expect(page.getByText(OPERATOR)).toBeVisible();
  const firstToken = storedRefreshToken();
  expect(firstToken).toBeTruthy();

  await page.evaluate(() => sessionStorage.clear());
  await page.goto("/");
  await page.getByRole("link", { name: /sign in with google/i }).click();
  await page.getByRole("link", { name: "Approve without refresh token" }).click();

  await expect(page.getByText(OPERATOR)).toBeVisible();
  expect(storedRefreshToken()).toBe(firstToken);
});
