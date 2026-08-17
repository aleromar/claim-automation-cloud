import { useEffect, useState } from "react";

import { apiUrl } from "./api";
import { authFetch } from "./auth";

type TrelloState = {
  api_key_stored: boolean;
  token_stored: boolean;
  board_id: string;
  list_id: string;
};
type GmailState = { account_email: string; refresh_token_stored: boolean };
// Discriminated union per the structure.md data-fetching pattern.
type Page =
  | { status: "loading" }
  | { status: "ok"; trello: TrelloState; gmail: GmailState }
  | { status: "error" };

const SETTINGS_URL = apiUrl("/api/settings");
const TRELLO_URL = apiUrl("/api/settings/trello");
const RECONNECT_URL = apiUrl("/api/auth/reconnect");

function isTrelloState(value: unknown): value is TrelloState {
  const trello = value as TrelloState;
  return (
    typeof trello === "object" &&
    trello !== null &&
    typeof trello.api_key_stored === "boolean" &&
    typeof trello.token_stored === "boolean" &&
    typeof trello.board_id === "string" &&
    typeof trello.list_id === "string"
  );
}

function isGmailState(value: unknown): value is GmailState {
  const gmail = value as GmailState;
  return (
    typeof gmail === "object" &&
    gmail !== null &&
    typeof gmail.account_email === "string" &&
    typeof gmail.refresh_token_stored === "boolean"
  );
}

const storedBadge = (stored: boolean) => (stored ? "(stored)" : "(not set)");

export default function Settings() {
  const [page, setPage] = useState<Page>({ status: "loading" });
  // Secret inputs are write-only: they start (and reset to) empty; the badge
  // is the only echo of what's stored (REQ-1.2/4.6).
  const [apiKey, setApiKey] = useState("");
  const [token, setToken] = useState("");
  const [boardId, setBoardId] = useState("");
  const [listId, setListId] = useState("");
  const [busy, setBusy] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    authFetch(SETTINGS_URL)
      .then((res) => {
        if (!res.ok) throw new Error(`unexpected status ${res.status}`);
        return res.json();
      })
      .then((body: { trello?: unknown; gmail?: unknown }) => {
        if (cancelled) return;
        // A 200 with a broken contract is an error state, not success.
        if (isTrelloState(body.trello) && isGmailState(body.gmail)) {
          setPage({ status: "ok", trello: body.trello, gmail: body.gmail });
          setBoardId(body.trello.board_id);
          setListId(body.trello.list_id);
        } else {
          setPage({ status: "error" });
        }
      })
      .catch(() => {
        if (!cancelled) setPage({ status: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const save = async () => {
    setBusy(true);
    setSaveFailed(false);
    try {
      const res = await authFetch(TRELLO_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          api_key: apiKey,
          token,
          board_id: boardId,
          list_id: listId,
        }),
      });
      if (!res.ok) throw new Error(`unexpected status ${res.status}`);
      const body: unknown = await res.json();
      if (!isTrelloState(body)) throw new Error("broken contract");
      // The response is the new truth (REQ-2.4): re-render badges/IDs from it
      // and clear the secret inputs back to their write-only resting state.
      setPage((current) =>
        current.status === "ok" ? { ...current, trello: body } : current,
      );
      setBoardId(body.board_id);
      setListId(body.list_id);
      setApiKey("");
      setToken("");
    } catch {
      // Writes are idempotent (secrets → table, fixed order): a retry of the
      // same save converges — say so instead of leaving the operator guessing.
      setSaveFailed(true);
    } finally {
      setBusy(false);
    }
  };

  if (page.status === "loading") {
    return (
      <article>
        <h2>Settings</h2>
        <p aria-busy="true">Loading settings…</p>
      </article>
    );
  }
  if (page.status === "error") {
    return (
      <article>
        <h2>Settings</h2>
        <p role="alert">⚠️ Settings unavailable</p>
      </article>
    );
  }
  return (
    <article>
      <h2>Settings</h2>
      <section>
        <h3>Trello</h3>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <label>
            API key {storedBadge(page.trello.api_key_stored)}
            <input
              type="password"
              autoComplete="new-password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              disabled={busy}
              placeholder="leave blank to keep the stored key"
            />
          </label>
          <label>
            API token {storedBadge(page.trello.token_stored)}
            <input
              type="password"
              autoComplete="new-password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              disabled={busy}
              placeholder="leave blank to keep the stored token"
            />
          </label>
          <label>
            Board ID
            <input
              type="text"
              value={boardId}
              onChange={(event) => setBoardId(event.target.value)}
              disabled={busy}
            />
          </label>
          <label>
            List ID
            <input
              type="text"
              value={listId}
              onChange={(event) => setListId(event.target.value)}
              disabled={busy}
            />
          </label>
          <button type="submit" disabled={busy} aria-busy={busy}>
            Save Trello settings
          </button>
          {saveFailed && (
            <p role="alert">
              ⚠️ Save failed — nothing was lost and it is safe to retry.
            </p>
          )}
        </form>
      </section>
      <section>
        <h3>Gmail</h3>
        <p>
          Account: <strong>{page.gmail.account_email}</strong>
          <br />
          Refresh token:{" "}
          {page.gmail.refresh_token_stored ? "stored" : "not set"}
        </p>
        {/* Top-level navigation on purpose: the consent flow must leave the
            SPA, and navigations cannot carry the Bearer header (REQ-3.4). */}
        <a href={RECONNECT_URL} role="button" className="secondary">
          Reconnect Gmail
        </a>
      </section>
    </article>
  );
}
