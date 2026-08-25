// Real-Gmail helper for the live suite (Node-side only — never page.request,
// so nothing credential-bearing enters Playwright traces). Scope ceiling is
// gmail.modify: insert/trash/list/modify are in-scope, permanent delete is not
// (verified against the API reference — spec P10 evidence).

import { requireLiveEnv } from "./env";

const API = "https://gmail.googleapis.com/gmail/v1/users/me";
const TOKEN_URL = "https://oauth2.googleapis.com/token";

// Mirrors pipeline.entry.build_claim_query() over CLAIM_SUBJECT_MARKERS —
// the sweep must see exactly what the pipeline will see.
export const CLAIM_MARKER_QUERY = [
  "Declaración de siniestro a colaborador",
  "Solicitud de asistencia a colaborador",
  "Comunicación a colaborador",
]
  .map((marker) => `subject:"${marker}"`)
  .join(" OR ");

interface MessageRef {
  id: string;
}

export class GmailLive {
  private accessToken: string | null = null;

  /** Mint an access token from the refresh token (same grant GmailClient uses). */
  private async token(): Promise<string> {
    if (this.accessToken) return this.accessToken;
    const env = requireLiveEnv();
    const res = await fetch(TOKEN_URL, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "refresh_token",
        refresh_token: env.GMAIL_REFRESH_TOKEN,
        client_id: env.GOOGLE_CLIENT_ID,
        client_secret: env.GOOGLE_CLIENT_SECRET,
      }),
    });
    if (!res.ok) {
      // Never include the response body: Google error payloads can echo request fields.
      throw new Error(`gmail: token refresh failed (HTTP ${res.status})`);
    }
    this.accessToken = ((await res.json()) as { access_token: string }).access_token;
    return this.accessToken;
  }

  private async call(method: string, path: string, body?: unknown): Promise<unknown> {
    const res = await fetch(`${API}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${await this.token()}`,
        ...(body !== undefined ? { "content-type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      throw new Error(`gmail: ${method} ${path.split("?")[0]} failed (HTTP ${res.status})`);
    }
    return res.json();
  }

  async listUnreadClaimIds(): Promise<string[]> {
    const params = new URLSearchParams({ q: CLAIM_MARKER_QUERY, labelIds: "UNREAD" });
    const data = (await this.call("GET", `/messages?${params}`)) as {
      messages?: MessageRef[];
    };
    return (data.messages ?? []).map((m) => m.id);
  }

  /** Pre-run sweep (REQ-2.1): trash every UNREAD claim email a crashed run left. */
  async sweepUnreadClaimEmails(): Promise<number> {
    const ids = await this.listUnreadClaimIds();
    for (const id of ids) await this.trash(id);
    return ids.length;
  }

  /** Seed the claim email: instant, searchable-eventually, labels set directly. */
  async insertClaimEmail(subject: string, htmlBody: string): Promise<string> {
    const account = requireLiveEnv().GMAIL_ACCOUNT;
    // RFC 2047 length caps are exceeded by the long Spanish subject as a single
    // encoded-word; Gmail (the only consumer) accepts it — proven live 2026-08-25.
    const encodedSubject = `=?UTF-8?B?${Buffer.from(subject).toString("base64")}?=`;
    const bodyB64 = Buffer.from(htmlBody).toString("base64");
    const mime = [
      `From: ${account}`,
      `To: ${account}`,
      `Subject: ${encodedSubject}`,
      "MIME-Version: 1.0",
      'Content-Type: text/html; charset="UTF-8"',
      "Content-Transfer-Encoding: base64",
      "",
      // RFC 2045 caps encoded lines at 76 chars — one giant line worked against
      // today's Gmail, but folding is cheap insurance.
      ...(bodyB64.match(/.{1,76}/g) ?? []),
    ].join("\r\n");
    // internalDateSource pinned: insert DEFAULTS to receivedTime (= now, what the
    // pipeline's date-ordering needs) but messages.import defaults dateHeader —
    // explicit beats a silent-refactor hazard.
    const data = (await this.call("POST", "/messages?internalDateSource=receivedTime", {
      raw: Buffer.from(mime).toString("base64url"),
      labelIds: ["UNREAD", "INBOX"],
    })) as MessageRef;
    return data.id;
  }

  /** Gmail's q-search index lags inserts; the pipeline lists via q, so wait
   * until the seeded message is actually searchable before Process now. */
  async waitUntilSearchable(messageId: string, budgetMs = 60_000): Promise<void> {
    const deadline = Date.now() + budgetMs;
    while (Date.now() < deadline) {
      if ((await this.listUnreadClaimIds()).includes(messageId)) return;
      await new Promise((resolve) => setTimeout(resolve, 2_000));
    }
    throw new Error(`gmail: seeded message not searchable after ${budgetMs}ms`);
  }

  async labelNames(messageId: string): Promise<string[]> {
    const msg = (await this.call(
      "GET",
      `/messages/${messageId}?format=minimal`,
    )) as { labelIds?: string[] };
    const labels = (await this.call("GET", "/labels")) as {
      labels: { id: string; name: string }[];
    };
    const byId = new Map(labels.labels.map((l) => [l.id, l.name]));
    return (msg.labelIds ?? []).map((id) => byId.get(id) ?? id);
  }

  async trash(messageId: string): Promise<void> {
    await this.call("POST", `/messages/${messageId}/trash`);
  }
}
