import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

import { seedTrelloSettings, setWorkerEnabled } from "./helpers/api";
import { requireLiveEnv } from "./helpers/env";
import { GmailLive } from "./helpers/gmail";
import { injectSession, mintSessionJwt } from "./helpers/session";
import { TrelloLive } from "./helpers/trello";

// Live smoke (live-e2e REQ-1/2): ALL SIX claim types through the REAL pipeline
// in one batch run — real Gmail, real Trello, deployed staging. Lifecycle:
// sweep → mint refs (per attempt) → seed 6 → poll searchable → settings →
// worker ON (UI) → Process now → assert UI + per-type Trello + Gmail → teardown.
// The comunicación email reuses the declaración's ref and is seeded LAST:
// internalDate ordering guarantees the card exists when its comment arrives
// (find_card_by_claim_ref scans board lists directly — no search-index lag).

// The canonical full-field parseable body (test_claim_parsing.py) — PLAIN_BODY
// lacks Localidad: and would crash build_card_name via town=None.
const ASITUR_BODY = readFileSync(
  fileURLToPath(
    new URL("../../backend/tests/data/asitur_xhtml_sample.html", import.meta.url),
  ),
  "utf8",
);

// Asistencia subtypes are classified by BODY markers (claim_data.from_subject);
// inject the marker into the parseable body, inside <body> when present.
const withMarker = (marker: string): string =>
  ASITUR_BODY.includes("</body>")
    ? ASITUR_BODY.replace("</body>", `<p>pide ${marker}</p></body>`)
    : `${ASITUR_BODY}<p>pide ${marker}</p>`;

const gmail = new GmailLive();
const trello = new TrelloLive();

// Teardown state — appended as the test progresses so afterEach can clean up
// whatever exists at the point of failure.
let claimRefs: string[] = [];
let seededMessageIds: string[] = [];
let jwt: string | null = null;

test.afterEach(async () => {
  // The hook gets its own budget: a test that burned its full timeout must not
  // starve cleanup (Gate 3 #6 — leftover emails + worker ON poison the next run).
  test.setTimeout(120_000);
  // Best-effort, every step attempted; failures aggregate so a broken teardown
  // is visible instead of silently poisoning the next run.
  const failures: string[] = [];
  if (jwt) {
    // Guarded: a pre-mint failure means the worker was never touched, and an
    // unset jwt here would 401 and mask the real error (Gate 3 #3).
    try {
      await setWorkerEnabled(jwt, false); // fail-safe for the shared staging env
    } catch (error) {
      failures.push(`worker OFF: ${String(error)}`);
    }
  }
  for (const id of seededMessageIds) {
    try {
      await gmail.trash(id);
    } catch (error) {
      failures.push(`trash email ${id}: ${String(error)}`);
    }
  }
  for (const ref of claimRefs) {
    try {
      for (const card of await trello.openCardsWithRef(ref)) {
        await trello.archiveCard(card.id);
      }
    } catch (error) {
      failures.push(`archive cards for ${ref}: ${String(error)}`);
    }
  }
  if (failures.length > 0) {
    throw new Error(`live teardown incomplete — ${failures.join("; ")}`);
  }
});

test("all six claim types flow through the real pipeline in one run", async ({
  page,
}) => {
  // Reset per attempt: a retry must not inherit attempt 1's teardown state.
  claimRefs = [];
  seededMessageIds = [];
  // JWT first — it needs no live call, and teardown's worker-OFF depends on it.
  jwt = mintSessionJwt();

  // Sweep (REQ-2.1): a crashed prior run's UNREAD residue would be processed
  // alongside our emails and break the exact-count assertion.
  await gmail.sweepUnreadClaimEmails();

  // Refs minted INSIDE the test body (REQ-2.3): a Playwright retry must get
  // fresh refs, or it dedup-skips against attempt 1's ledger rows.
  const year = String(new Date().getFullYear());
  const base = Math.floor(Date.now() / 1000);
  const ref = (offset: number): string => {
    const r = `${year}/${base + offset}`;
    claimRefs.push(r);
    return r;
  };
  const observaciones = `seguimiento live-e2e ${base}`;

  // One email per type; card-creating types get distinct refs, the comunicación
  // reuses the declaración's (it comments on that card, creates nothing).
  // Expected comments pin the build_card_comment taxonomy — including that
  // ELECTRICIDAD deliberately falls through to the default "Parte nuevo".
  const declaracionRef = ref(0);
  const seeds: {
    subject: string;
    body: string;
    cardRef: string | null; // null = no card of its own (comunicación)
    comment: RegExp;
  }[] = [
    {
      subject: `AVISO: Declaración de siniestro a colaborador ${declaracionRef}`,
      body: ASITUR_BODY,
      cardRef: declaracionRef,
      comment: /^@board Parte nuevo en /,
    },
    {
      // URGENTE rides AFTER the intact marker phrase (classification fixture
      // format) — "de siniestro urgente a" would match neither the Gmail
      // phrase query nor from_subject's marker check.
      subject: `AVISO: Declaración de siniestro a colaborador ${ref(1)} URGENTE`,
      body: ASITUR_BODY,
      cardRef: claimRefs[1],
      comment: /^@board Parte URGENTE en /,
    },
    {
      subject: `Solicitud de asistencia a colaborador ${ref(2)}`,
      body: withMarker("SERVICIO BRICO HOGAR"),
      cardRef: claimRefs[2],
      comment: /^@board Nueva brico asistencia en /,
    },
    {
      subject: `Solicitud de asistencia a colaborador ${ref(3)}`,
      body: withMarker("ENVÍO DE PROFESIONALES"),
      cardRef: claimRefs[3],
      comment: /^@board Nuevo envío de profesionales en /,
    },
    {
      subject: `Solicitud de asistencia a colaborador ${ref(4)}`,
      body: withMarker("ELECTRICIDAD DE EMERGENCIA"),
      cardRef: claimRefs[4],
      comment: /^@board Parte nuevo en /,
    },
    {
      // Seeded LAST: must process after the declaración created its card.
      // Full <html> document: the pipeline's HTML→plain conversion triggers on
      // document-shaped bodies only — a bare <p> fragment leaks its tags into
      // the extracted observaciones (found live: "…</p>" in the comment).
      subject: `Comunicación a colaborador ${declaracionRef}`,
      body: `<html><body><p>Observaciones: ${observaciones}</p></body></html>`,
      cardRef: null,
      comment: new RegExp(`^@board ${observaciones}$`),
    },
  ];

  for (const seed of seeds) {
    seededMessageIds.push(await gmail.insertClaimEmail(seed.subject, seed.body));
  }
  // Gmail's q-search index lags inserts; the pipeline lists via q (REQ-1.1).
  await gmail.waitUntilSearchable(seededMessageIds);

  await seedTrelloSettings(jwt);
  await injectSession(page, jwt);
  await page.goto("/");
  await expect(page.getByText(requireLiveEnv().GMAIL_ACCOUNT)).toBeVisible();

  // Spanish UI (frontend-spanish): literals duplicated on purpose, same
  // language-contract stance as the PR suite.
  const workerSwitch = page.getByRole("switch", { name: /proceso activado/i });
  await expect(workerSwitch).toBeVisible();
  if (!(await workerSwitch.isChecked())) {
    await workerSwitch.click();
  }
  await expect(workerSwitch).toBeChecked();

  // Synchronous run: the response returns only when the pipeline finishes.
  // A pipeline failure 500s the request and the outcome span never renders —
  // wait for EITHER the outcome or the run-failure alert, so a broken run
  // fails in seconds with a reason instead of a 200s blind poll (Gate 3 #2).
  // The alert is matched by its TEXT, not role=alert: the ReconnectBanner and
  // the metrics panel are alerts too, and a banner left over from a previous
  // failed run matched instantly while the wake was still in flight (staging
  // gate run 2, Bugfix log).
  await page.getByRole("button", { name: /procesar ahora/i }).click();
  const runFailed = page.getByText(/la acción ha fallado/i);
  await expect(page.getByText(/resultado:/i).or(runFailed)).toBeVisible({
    timeout: 200_000,
  });
  if (await runFailed.isVisible()) {
    throw new Error("process-now returned an error — run-failure alert shown");
  }
  await expect(page.getByText(/resultado:/i)).toHaveText(/resultado: completado/i, {
    timeout: 5_000,
  });
  await expect(page.getByText(/última ejecución:/i)).toContainText(
    /6 procesados, 0 fallidos/,
  );

  // Trello (REQ-1.2), ref-scoped per type: exactly one card each, on the
  // configured list, carrying its PDF and its @board notification comment.
  for (const seed of seeds.filter((s) => s.cardRef !== null)) {
    const cards = await trello.openCardsWithRef(seed.cardRef as string);
    expect(cards, seed.subject).toHaveLength(1);
    expect(cards[0].idList, seed.subject).toBe(requireLiveEnv().TRELLO_LIST_ID);
    const num = (seed.cardRef as string).split("/")[1];
    const attachments = await trello.attachmentNames(cards[0].id);
    expect(attachments, seed.subject).toContain(`claim_${num}_${year}.pdf`);
    const comments = await trello.cardComments(cards[0].id);
    expect(
      comments.some((c) => seed.comment.test(c)),
      `${seed.subject}: expected a comment matching ${seed.comment}`,
    ).toBe(true);
  }

  // REQ-8 chaining: the comunicación's observaciones landed as a SECOND
  // comment on the declaración's card — and created no card of its own
  // (the ref-scoped toHaveLength(1) above already pinned that).
  const declaracionCard = (await trello.openCardsWithRef(declaracionRef))[0];
  const declaracionComments = await trello.cardComments(declaracionCard.id);
  expect(
    declaracionComments.some((c) => c === `@board ${observaciones}`),
    "comunicación comment missing on the declaración card",
  ).toBe(true);

  // Gmail (REQ-1.3), every seeded message: UNREAD gone, procesado present
  // (created lowercase on a fresh mailbox; lookup is case-insensitive).
  for (const id of seededMessageIds) {
    const labels = (await gmail.labelNames(id)).map((name) => name.toLowerCase());
    expect(labels, id).not.toContain("unread");
    expect(labels, id).toContain("procesado");
  }
});
