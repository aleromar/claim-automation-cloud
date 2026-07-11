import { expect, test } from "@playwright/test";

// REQ-4: the full wiring — real browser → Vite → proxy → uvicorn/FastAPI — shows "All good".
test("dashboard reports the backend is healthy", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/all good/i)).toBeVisible();
});
