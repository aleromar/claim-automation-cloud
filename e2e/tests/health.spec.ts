import { expect, test } from "@playwright/test";

// Reworked for the auth gate (auth spec REQ-6.2): an unauthenticated page load
// shows the login screen; backend liveness is asserted on the API directly.
test("unauthenticated visit shows the login screen, no dashboard data", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("link", { name: /iniciar sesión con google/i })).toBeVisible();
  await expect(
    page.getByRole("switch", { name: /proceso activado/i }),
  ).not.toBeVisible();
});

test("backend liveness endpoint stays public (auth spec REQ-3.3; body amended by version-display REQ-1)", async ({ request }) => {
  const res = await request.get("http://localhost:8000/api/health");
  expect(res.ok()).toBeTruthy();
  const body = await res.json();
  // Shape-only on version: uvicorn normally reports "dev", but a stray local
  // stamp file must not redden e2e (version-display spec, review gate R-3).
  expect(body).toMatchObject({ status: "ok" });
  expect(typeof body.version).toBe("string");
  expect(body.version.length).toBeGreaterThan(0);
});

test("protected API rejects unauthenticated calls", async ({ request }) => {
  const res = await request.get("http://localhost:8000/api/me");
  expect(res.status()).toBe(401);
});
