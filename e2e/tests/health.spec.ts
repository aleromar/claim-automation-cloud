import { expect, test } from "@playwright/test";

// Reworked for the auth gate (auth spec REQ-6.2): an unauthenticated page load
// shows the login screen; backend liveness is asserted on the API directly.
test("unauthenticated visit shows the login screen, no dashboard data", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("link", { name: /sign in with google/i })).toBeVisible();
  await expect(page.getByText(/all good/i)).not.toBeVisible();
});

test("backend liveness endpoint stays public (REQ-3.3)", async ({ request }) => {
  const res = await request.get("http://localhost:8000/api/health");
  expect(res.ok()).toBeTruthy();
  expect(await res.json()).toEqual({ status: "ok" });
});

test("protected API rejects unauthenticated calls", async ({ request }) => {
  const res = await request.get("http://localhost:8000/api/me");
  expect(res.status()).toBe(401);
});
