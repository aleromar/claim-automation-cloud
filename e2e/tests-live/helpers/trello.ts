// Real-Trello helper (Node-side only). Auth rides in the Authorization header
// (the backend client's pattern — trello_client.py) so key/token never appear
// in URLs, and error messages carry status codes only.

import { requireLiveEnv } from "./env";

const API = "https://api.trello.com/1";

export interface CardSummary {
  id: string;
  name: string;
  idList: string;
}

export class TrelloLive {
  private authHeader(): string {
    const env = requireLiveEnv();
    return `OAuth oauth_consumer_key="${env.TRELLO_API_KEY}", oauth_token="${env.TRELLO_TOKEN}"`;
  }

  private async call(method: string, path: string, what: string): Promise<unknown> {
    const res = await fetch(`${API}${path}`, {
      method,
      headers: { Authorization: this.authHeader() },
    });
    if (!res.ok) {
      // `what`, never the path: URL segments embed the board id, which is
      // configured as a GitHub secret (REQ-4.4 — CI masks it, local runs don't).
      throw new Error(`trello: ${what} failed (HTTP ${res.status})`);
    }
    return res.json();
  }

  /** Ref-scoped lookup (REQ-2.3): never "the list is empty" — a crashed prior
   * run can leave sweep-immune residue cards on the test board. */
  async openCardsWithRef(claimRef: string): Promise<CardSummary[]> {
    const env = requireLiveEnv();
    const cards = (await this.call(
      "GET",
      `/boards/${env.TRELLO_BOARD_ID}/cards/open?fields=id,name,idList`,
      "list open board cards",
    )) as CardSummary[];
    return cards.filter((card) => card.name.includes(claimRef));
  }

  async attachmentNames(cardId: string): Promise<string[]> {
    const attachments = (await this.call(
      "GET",
      `/cards/${cardId}/attachments?fields=name`,
      "read card attachments",
    )) as { name: string }[];
    return attachments.map((a) => a.name);
  }

  async archiveCard(cardId: string): Promise<void> {
    await this.call("PUT", `/cards/${cardId}?closed=true`, "archive card");
  }
}
