import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

import { seedTrelloSettings, setWorkerEnabled } from "./helpers/api";
import { requireLiveEnv } from "./helpers/env";
import { GmailLive } from "./helpers/gmail";
import { injectSession, mintSessionJwt } from "./helpers/session";
import { TrelloLive } from "./helpers/trello";

// Live smoke (live-e2e REQ-1/2): one declaración email through the REAL
// pipeline — real Gmail, real Trello, local stack. Lifecycle:
// sweep → mint ref (per attempt) → seed → poll searchable → settings →
// worker ON (UI) → Process now → assert UI + Trello + Gmail → teardown.

// The canonical full-field parseable body (test_claim_parsing.py) — PLAIN_BODY
// lacks Localidad: and would crash build_card_name via town=None.
const ASITUR_BODY = readFileSync(
  fileURLToPath(
    new URL("../../backend/tests/data/asitur_xhtml_sample.html", import.meta.url),
  ),
  "utf8",
);

const gmail = new GmailLive();
const trello = new TrelloLive();

// Teardown state — set as the test progresses so afterEach can clean up
// whatever exists at the point of failure.
let claimRef: string | null = null;
let seededMessageId: string | null = null;
let jwt: string | null = null;

test.afterEach(async () => {
  // The hook gets its own budget: a test that burned its full timeout must not
  // starve cleanup (Gate 3 #6 — leftover email + worker ON poison the next run).
  test.setTimeout(60_000);
  // Best-effort, every step attempted; failures aggregate so a broken teardown
  // is visible instead of silently poisoning the next run.
  const failures: string[] = [];
  if (jwt) {
    // Guarded: a pre-mint failure means the worker was never touched, and an
    // unset jwt here would 401 and mask the real error (Gate 3 #3).
    try {
      await setWorkerEnabled(jwt, false); // fail-safe for the shared local Azurite
    } catch (error) {
      failures.push(`worker OFF: ${String(error)}`);
    }
  }
  if (seededMessageId) {
    try {
      await gmail.trash(seededMessageId);
    } catch (error) {
      failures.push(`trash email: ${String(error)}`);
    }
  }
  if (claimRef) {
    try {
      for (const card of await trello.openCardsWithRef(claimRef)) {
        await trello.archiveCard(card.id);
      }
    } catch (error) {
      failures.push(`archive card: ${String(error)}`);
    }
  }
  if (failures.length > 0) {
    throw new Error(`live teardown incomplete — ${failures.join("; ")}`);
  }
});

test("a seeded claim email becomes a Trello card through the real pipeline", async ({
  page,
}) => {
  // Reset per attempt: a retry must not inherit attempt 1's teardown state.
  claimRef = null;
  seededMessageId = null;
  // JWT first — it needs no live call, and teardown's worker-OFF depends on it.
  jwt = mintSessionJwt();

  // Sweep (REQ-2.1): a crashed prior run's UNREAD residue would be processed
  // alongside our email and break the exact-count assertion.
  await gmail.sweepUnreadClaimEmails();

  // Ref minted INSIDE the test body (REQ-2.3): a Playwright retry must get a
  // fresh ref, or it dedup-skips against attempt 1's ledger row.
  const year = String(new Date().getFullYear());
  const num = String(Math.floor(Date.now() / 1000));
  claimRef = `${year}/${num}`;

  seededMessageId = await gmail.insertClaimEmail(
    `AVISO: Declaración de siniestro a colaborador ${claimRef}`,
    ASITUR_BODY,
  );
  // Gmail's q-search index lags inserts; the pipeline lists via q (REQ-1.1).
  await gmail.waitUntilSearchable(seededMessageId);

  await seedTrelloSettings(jwt);
  await injectSession(page, jwt);
  await page.goto("/");
  await expect(page.getByText(requireLiveEnv().GMAIL_ACCOUNT)).toBeVisible();

  const workerSwitch = page.getByRole("switch", { name: /worker enabled/i });
  await expect(workerSwitch).toBeVisible();
  if (!(await workerSwitch.isChecked())) {
    await workerSwitch.click();
  }
  await expect(workerSwitch).toBeChecked();

  // Synchronous run: the response returns only when the pipeline finishes.
  // A pipeline failure 500s the request and the outcome span never renders —
  // wait for EITHER the outcome or the failure alert, so a broken run fails in
  // seconds with a reason instead of a 200s blind poll (Gate 3 #2).
  await page.getByRole("button", { name: /process now/i }).click();
  await expect(
    page.getByText(/run result:/i).or(page.getByRole("alert")),
  ).toBeVisible({ timeout: 200_000 });
  await expect(page.getByText(/run result:/i)).toHaveText(/run result: ran/i, {
    timeout: 5_000,
  });
  await expect(page.getByText(/last run:/i)).toContainText(/1 processed, 0 failed/);

  // Trello (REQ-1.2), ref-scoped: exactly our card, on the configured list,
  // carrying the PDF.
  const cards = await trello.openCardsWithRef(claimRef);
  expect(cards).toHaveLength(1);
  expect(cards[0].idList).toBe(requireLiveEnv().TRELLO_LIST_ID);
  const attachments = await trello.attachmentNames(cards[0].id);
  expect(attachments).toContain(`claim_${num}_${year}.pdf`);

  // Gmail (REQ-1.3): UNREAD gone, procesado present (created lowercase on a
  // fresh mailbox; lookup is case-insensitive — assert likewise).
  const labels = (await gmail.labelNames(seededMessageId)).map((name) =>
    name.toLowerCase(),
  );
  expect(labels).not.toContain("unread");
  expect(labels).toContain("procesado");
});
