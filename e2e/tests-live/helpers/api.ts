// Backend API calls the live suite makes outside the browser: Trello settings
// seeding (the real operator write path — settings_routes.py POST /trello) and
// the worker-OFF teardown fail-safe (robust even when the UI is wedged).

import { requireLiveEnv } from "./env";

const BACKEND = "http://localhost:8000/api";

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
