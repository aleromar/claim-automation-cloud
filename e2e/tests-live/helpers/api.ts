// Backend API calls the live suite makes outside the browser: Trello settings
// seeding (the real operator write path — settings_routes.py POST /trello) and
// the worker-OFF teardown fail-safe (robust even when the UI is wedged).
// Targets the deployed staging function app (staging-environment REQ-4.1).

import { requireLiveEnv } from "./env";

const BACKEND = requireLiveEnv().LIVE_BACKEND_API_URL;

async function authedPost(jwt: string, path: string, body: unknown): Promise<void> {
  const res = await fetch(`${BACKEND}${path}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${jwt}`,
      "content-type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`backend: POST ${path} failed (HTTP ${res.status})`);
  }
}

/** Seeds credentials into the secret store AND board/list into the state store. */
export async function seedTrelloSettings(jwt: string): Promise<void> {
  const env = requireLiveEnv();
  await authedPost(jwt, "/settings/trello", {
    api_key: env.TRELLO_API_KEY,
    token: env.TRELLO_TOKEN,
    board_id: env.TRELLO_BOARD_ID,
    list_id: env.TRELLO_LIST_ID,
  });
}

export async function setWorkerEnabled(jwt: string, enabled: boolean): Promise<void> {
  await authedPost(jwt, "/worker/enabled", { enabled });
}

/** The attachment zip straight from the API (attachment-download REQ-5) —
 * the browser path is exercised separately through the /descargas page. */
export async function downloadClaimAttachments(
  jwt: string,
  claimRef: string,
): Promise<Uint8Array> {
  const [year, number] = claimRef.split("/");
  const res = await fetch(`${BACKEND}/claims/${year}/${number}/attachments`, {
    headers: { Authorization: `Bearer ${jwt}` },
  });
  if (!res.ok) {
    throw new Error(`backend: GET claim attachments failed (HTTP ${res.status})`);
  }
  return new Uint8Array(await res.arrayBuffer());
}
