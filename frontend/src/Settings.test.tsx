import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import Settings, { type TrelloSaveRequest } from "./Settings";
import {
  API_KEY_LABEL,
  API_TOKEN_LABEL,
  BOARD_ID_LABEL,
  LIST_ID_LABEL,
  NOT_SET_BADGE,
  RECONNECT_GMAIL,
  REFRESH_TOKEN_LABEL,
  SAVE_FAILED,
  SAVE_TRELLO,
  SETTINGS_UNAVAILABLE,
  STORED_BADGE,
  TOKEN_STORED,
} from "./strings";

const OPERATOR = "operator@example.com";

// Label text is "<field> <badge>" (e.g. "Clave API (guardado)") — the badge
// constants carry literal parens, so build a safe regex instead of a raw one.
const escapeRegExp = (value: string) =>
  value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const labelWithBadge = (label: string, badge: string) =>
  new RegExp(`${escapeRegExp(label)}.*${escapeRegExp(badge)}`, "i");

const settingsBody = {
  trello: {
    api_key_stored: true,
    token_stored: false,
    board_id: "board-1",
    list_id: "list-1",
  },
  gmail: { account_email: OPERATOR, refresh_token_stored: true },
};

// The component's own body contract — a backend-driven field rename must
// break these tests at compile time, not ship a silent ID wipe.
type SaveBody = TrelloSaveRequest;

// Factories, not shared Response objects: a Response body reads only once
// (worker-controls gate ER-W4).
function mockSettingsApi({
  state = () => new Response(JSON.stringify(settingsBody), { status: 200 }),
  save = (body: SaveBody) =>
    new Response(
      JSON.stringify({
        api_key_stored: true,
        token_stored: true,
        board_id: body.board_id,
        list_id: body.list_id,
      }),
      { status: 200 },
    ),
}: {
  state?: () => Response | Promise<Response>;
  save?: (body: SaveBody) => Response | Promise<Response>;
} = {}) {
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(async (input, init) => {
      const url = String(input);
      // Longest prefix first: /api/settings is a prefix of /api/settings/trello.
      if (url.includes("/api/settings/trello"))
        return save(JSON.parse(String(init?.body)));
      if (url.includes("/api/settings")) return state();
      throw new Error(`unexpected fetch: ${url}`);
    });
}

describe("Settings load state (REQ-4.2)", () => {
  it("prefills the IDs, shows per-secret badges and the Gmail account", async () => {
    mockSettingsApi();
    render(<Settings />);
    expect(
      await screen.findByLabelText(labelWithBadge(API_KEY_LABEL, STORED_BADGE)),
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText(labelWithBadge(API_TOKEN_LABEL, NOT_SET_BADGE)),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(new RegExp(BOARD_ID_LABEL, "i"))).toHaveValue(
      "board-1",
    );
    expect(screen.getByLabelText(new RegExp(LIST_ID_LABEL, "i"))).toHaveValue(
      "list-1",
    );
    expect(screen.getByText(OPERATOR)).toBeInTheDocument();
    expect(
      screen.getByText(
        new RegExp(`${REFRESH_TOKEN_LABEL} ${TOKEN_STORED}`, "i"),
      ),
    ).toBeInTheDocument();
  });

  it("renders secret inputs write-only: password type, autofill off, never prefilled (REQ-4.6)", async () => {
    mockSettingsApi();
    render(<Settings />);
    const apiKey = await screen.findByLabelText(new RegExp(API_KEY_LABEL, "i"));
    const token = screen.getByLabelText(new RegExp(API_TOKEN_LABEL, "i"));
    for (const input of [apiKey, token]) {
      expect(input).toHaveAttribute("type", "password");
      // Password managers autofill would defeat blank=keep and silently
      // overwrite stored credentials (P10 gate CRITICAL).
      expect(input).toHaveAttribute("autocomplete", "new-password");
      expect(input).toHaveValue("");
    }
  });

  it("links Reconnect Gmail to the backend navigation endpoint (REQ-4.4)", async () => {
    mockSettingsApi();
    render(<Settings />);
    const reconnect = await screen.findByRole("button", {
      name: RECONNECT_GMAIL,
    });
    expect(reconnect).toHaveAttribute("href", "/api/auth/reconnect");
  });

  it("shows the error state when the settings fetch fails", async () => {
    mockSettingsApi({ state: () => new Response("", { status: 503 }) });
    render(<Settings />);
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(SETTINGS_UNAVAILABLE),
    );
  });

  it("treats a 200 with a broken contract as the error state", async () => {
    mockSettingsApi({
      state: () =>
        new Response(JSON.stringify({ nope: true }), { status: 200 }),
    });
    render(<Settings />);
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(SETTINGS_UNAVAILABLE),
    );
  });
});

describe("Settings save (REQ-4.3/4.7)", () => {
  it("posts all four fields and re-renders from the response, clearing secrets", async () => {
    const saves: SaveBody[] = [];
    mockSettingsApi({
      save: (body) => {
        saves.push(body);
        return new Response(
          JSON.stringify({
            api_key_stored: true,
            token_stored: true,
            board_id: body.board_id,
            list_id: body.list_id,
          }),
          { status: 200 },
        );
      },
    });
    render(<Settings />);
    const token = await screen.findByLabelText(
      new RegExp(API_TOKEN_LABEL, "i"),
    );
    fireEvent.change(token, { target: { value: "tok-new" } });
    fireEvent.change(screen.getByLabelText(new RegExp(BOARD_ID_LABEL, "i")), {
      target: { value: "board-2" },
    });
    fireEvent.click(screen.getByRole("button", { name: SAVE_TRELLO }));

    // Blank api_key posts as "" — blank=keep is the backend's contract (REQ-2.2).
    await waitFor(() =>
      expect(saves).toEqual([
        {
          api_key: "",
          token: "tok-new",
          board_id: "board-2",
          list_id: "list-1",
        },
      ]),
    );
    // Response is the new truth: badge flips, secret input cleared.
    expect(
      await screen.findByLabelText(
        labelWithBadge(API_TOKEN_LABEL, STORED_BADGE),
      ),
    ).toHaveValue("");
  });

  it("disables the form while the save is in flight", async () => {
    let release: (r: Response) => void = () => {};
    mockSettingsApi({
      save: () => new Promise<Response>((resolve) => (release = resolve)),
    });
    render(<Settings />);
    await screen.findByLabelText(new RegExp(BOARD_ID_LABEL, "i"));
    fireEvent.click(screen.getByRole("button", { name: SAVE_TRELLO }));
    expect(screen.getByRole("button", { name: SAVE_TRELLO })).toBeDisabled();
    release(
      new Response(
        JSON.stringify({
          api_key_stored: true,
          token_stored: false,
          board_id: "board-1",
          list_id: "list-1",
        }),
        { status: 200 },
      ),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: SAVE_TRELLO })).toBeEnabled(),
    );
  });

  it("keeps the typed secrets in the inputs when the save fails", async () => {
    mockSettingsApi({ save: () => new Response("", { status: 500 }) });
    render(<Settings />);
    const token = await screen.findByLabelText(
      new RegExp(API_TOKEN_LABEL, "i"),
    );
    fireEvent.change(token, { target: { value: "tok-typed" } });
    fireEvent.click(screen.getByRole("button", { name: SAVE_TRELLO }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(SAVE_FAILED),
    );
    // Clearing on failure would make the retry post "" → blank=keep → the new
    // credential silently lost (P10 gate CRITICAL through a different door).
    expect(screen.getByLabelText(new RegExp(API_TOKEN_LABEL, "i"))).toHaveValue(
      "tok-typed",
    );
  });

  it("re-reads settings after a failed save: badges tell the stored truth, typed IDs survive", async () => {
    let stateCalls = 0;
    mockSettingsApi({
      state: () => {
        stateCalls += 1;
        // The second read simulates a partial save: the token landed in the
        // secret store before the table write failed.
        return new Response(
          JSON.stringify({
            ...settingsBody,
            trello: { ...settingsBody.trello, token_stored: stateCalls > 1 },
          }),
          { status: 200 },
        );
      },
      save: () => new Response("", { status: 500 }),
    });
    render(<Settings />);
    await screen.findByLabelText(
      labelWithBadge(API_TOKEN_LABEL, NOT_SET_BADGE),
    );
    fireEvent.change(screen.getByLabelText(new RegExp(BOARD_ID_LABEL, "i")), {
      target: { value: "board-edited" },
    });
    fireEvent.click(screen.getByRole("button", { name: SAVE_TRELLO }));

    // Never assume a write landed — re-read (the WorkerControls rule).
    expect(
      await screen.findByLabelText(
        labelWithBadge(API_TOKEN_LABEL, STORED_BADGE),
      ),
    ).toBeInTheDocument();
    // The re-read must not clobber operator input: retry re-sends these IDs.
    expect(screen.getByLabelText(new RegExp(BOARD_ID_LABEL, "i"))).toHaveValue(
      "board-edited",
    );
    expect(screen.getByRole("alert")).toHaveTextContent(SAVE_FAILED);
  });

  it("shows a retry-is-safe error state when the save fails (REQ-4.7)", async () => {
    mockSettingsApi({ save: () => new Response("", { status: 500 }) });
    render(<Settings />);
    await screen.findByLabelText(new RegExp(BOARD_ID_LABEL, "i"));
    fireEvent.click(screen.getByRole("button", { name: SAVE_TRELLO }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(SAVE_FAILED),
    );
  });
});
