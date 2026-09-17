import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AttachmentsDownload from "./AttachmentsDownload";
import {
  ATTACHMENTS_ERROR_LABELS,
  CLAIM_REF_LABEL,
  DOWNLOAD,
  DOWNLOAD_FAILED,
  DOWNLOADS_TITLE,
  downloadReadyText,
  INVALID_CLAIM_REF,
  NAV_SETTINGS,
} from "./strings";

// jsdom implements neither object URLs nor anchor navigation (gate ER-6): the
// blob path is asserted through these stubs, and the click spy keeps the
// "Not implemented: navigation" noise out of the run.
const createObjectURL = vi.fn<(blob: Blob) => string>(() => "blob:mock-url");
const revokeObjectURL = vi.fn();
let clickedDownloads: string[] = [];

beforeEach(() => {
  clickedDownloads = [];
  URL.createObjectURL = createObjectURL;
  URL.revokeObjectURL = revokeObjectURL;
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    clickedDownloads.push(this.download);
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  createObjectURL.mockClear();
  revokeObjectURL.mockClear();
});

// Factory, not a shared Response: a body reads only once (worker-controls gate ER-W4).
function mockDownloadApi(
  respond: () => Response | Promise<Response> = () =>
    new Response(new Uint8Array([0x50, 0x4b]), { status: 200 }),
) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("/api/claims/")) return respond();
    throw new Error(`unexpected fetch: ${url}`);
  });
}

function renderPage() {
  return render(
    <MemoryRouter>
      <AttachmentsDownload />
    </MemoryRouter>,
  );
}

async function submit(ref: string) {
  fireEvent.change(screen.getByLabelText(CLAIM_REF_LABEL), {
    target: { value: ref },
  });
  fireEvent.click(screen.getByRole("button", { name: DOWNLOAD }));
}

const errorJson = (status: number, detail: string) => () =>
  new Response(JSON.stringify({ detail }), { status });

describe("AttachmentsDownload page (REQ-3.1/3.2)", () => {
  it("renders the title, the claim input and the button", () => {
    renderPage();
    expect(
      screen.getByRole("heading", { name: DOWNLOADS_TITLE }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(CLAIM_REF_LABEL)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: DOWNLOAD })).toBeEnabled();
  });

  it.each(["2026-417", "26/417", "2026/", "abcd/417", "٢٠٢٦/١"])(
    "rejects %s inline without any fetch",
    async (ref) => {
      const spy = vi.spyOn(globalThis, "fetch");
      renderPage();
      await submit(ref);
      expect(screen.getByRole("alert")).toHaveTextContent(INVALID_CLAIM_REF);
      expect(spy).not.toHaveBeenCalled();
    },
  );
});

describe("AttachmentsDownload happy path (REQ-3.3)", () => {
  it("fetches the zip with auth, saves it via a blob anchor and reports the filename", async () => {
    const spy = mockDownloadApi();
    renderPage();
    await submit("2026/417");
    await screen.findByText(downloadReadyText("2026_417.zip"));
    const [url] = spy.mock.calls[0];
    expect(String(url)).toContain("/api/claims/2026/417/attachments");
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    // Node's Response.blob() and jsdom's global Blob are different classes, so
    // assert the blob's contents rather than its constructor.
    expect(createObjectURL.mock.calls[0][0].size).toBe(2);
    expect(clickedDownloads).toEqual(["2026_417.zip"]);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock-url");
  });

  it("disables the input and button while the download is in flight", async () => {
    mockDownloadApi(() => new Promise<Response>(() => {}));
    renderPage();
    await submit("2026/417");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: DOWNLOAD })).toBeDisabled(),
    );
    expect(screen.getByLabelText(CLAIM_REF_LABEL)).toBeDisabled();
  });
});

describe("AttachmentsDownload error mapping (REQ-3.4)", () => {
  it.each([
    [404, "card_not_found"],
    [404, "no_attachments"],
    [503, "trello_no_access"],
    [502, "trello_error"],
    [504, "timeout"],
  ] as const)(
    "maps %i %s to its fixed Spanish copy",
    async (status, detail) => {
      mockDownloadApi(errorJson(status, detail));
      renderPage();
      await submit("2026/417");
      await waitFor(() =>
        expect(screen.getByRole("alert")).toHaveTextContent(
          ATTACHMENTS_ERROR_LABELS[detail],
        ),
      );
      expect(createObjectURL).not.toHaveBeenCalled();
    },
  );

  it("links to Ajustes when Trello access is the problem", async () => {
    mockDownloadApi(errorJson(503, "trello_no_access"));
    renderPage();
    await submit("2026/417");
    const link = await screen.findByRole("link", { name: NAV_SETTINGS });
    expect(link).toHaveAttribute("href", "/settings");
  });

  it.each(["EVIL_FREE_TEXT", "constructor", "toString"])(
    "falls back to the generic message for unknown detail %s and never renders server text",
    async (detail) => {
      // "constructor"/"toString" are prototype keys of the label record: a
      // plain index lookup would return a function, not undefined (gate 3 I3).
      mockDownloadApi(errorJson(404, detail));
      renderPage();
      await submit("2026/417");
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent(DOWNLOAD_FAILED);
      expect(alert).not.toHaveTextContent(/EVIL_FREE_TEXT/);
    },
  );

  it("shows the generic message on a body-less failure (e.g. 401 or a dropped connection)", async () => {
    mockDownloadApi(() => new Response("", { status: 401 }));
    renderPage();
    await submit("2026/417");
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(DOWNLOAD_FAILED),
    );
  });

  it("clears a previous error when a new download succeeds", async () => {
    let calls = 0;
    mockDownloadApi(() =>
      calls++ === 0
        ? new Response(JSON.stringify({ detail: "card_not_found" }), {
            status: 404,
          })
        : new Response(new Uint8Array([0x50, 0x4b]), { status: 200 }),
    );
    renderPage();
    await submit("2026/417");
    await screen.findByRole("alert");
    await submit("2026/418");
    await screen.findByText(downloadReadyText("2026_418.zip"));
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
