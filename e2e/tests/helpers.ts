import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { expect, type Page } from "@playwright/test";

import { OPERATOR } from "./constants";

// The backend's file secret store — shared mutable dev state.
export const SECRETS_PATH = fileURLToPath(
  new URL("../.tmp/secrets.json", import.meta.url),
);

export const storedSecret = (name: string): string | undefined =>
  JSON.parse(readFileSync(SECRETS_PATH, "utf8"))[name];

/**
 * Write back (or remove) a secret. Tests that write the store must restore it:
 * the UI is deliberately unable to clear a credential, so a leftover fake
 * value would poison the shared dev store permanently.
 */
export const restoreSecret = (name: string, value: string | undefined) => {
  const secrets = JSON.parse(readFileSync(SECRETS_PATH, "utf8")) as Record<
    string,
    string
  >;
  if (value === undefined) delete secrets[name];
  else secrets[name] = value;
  writeFileSync(SECRETS_PATH, JSON.stringify(secrets));
};

/** Sign in through the stub IdP and wait for the authed shell. */
export const login = async (page: Page) => {
  await page.goto("/");
  await page.getByRole("link", { name: /sign in with google/i }).click();
  await page.getByRole("link", { name: "Approve", exact: true }).click();
  await expect(page.getByText(OPERATOR)).toBeVisible();
};
