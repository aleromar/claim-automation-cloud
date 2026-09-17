import { useState } from "react";
import { Link } from "react-router-dom";

import { apiUrl } from "./api";
import { authFetch } from "./auth";
import {
  ATTACHMENTS_ERROR_LABELS,
  type AttachmentsDetail,
  CLAIM_REF_LABEL,
  DOWNLOAD,
  DOWNLOAD_FAILED,
  DOWNLOADS_TITLE,
  downloadReadyText,
  INVALID_CLAIM_REF,
  NAV_SETTINGS,
  TRELLO_NO_ACCESS_DETAIL,
} from "./strings";

// Same shape as the laptop's input check and the backend's path patterns
// (attachment-download REQ-2.1/3.2): ASCII digits only, so both sides agree.
const CLAIM_REF_PATTERN = /^([0-9]{4})\/([0-9]+)$/;

// Discriminated union per the structure.md data-fetching pattern.
type Download =
  | { status: "idle" }
  | { status: "busy" }
  | { status: "done"; filename: string }
  | { status: "error"; message: string; settingsHint: boolean };

// Own-property check, not a bare index: a detail like "constructor" would
// otherwise resolve to a prototype member instead of falling back.
const errorLabel = (detail: unknown): string =>
  typeof detail === "string" && Object.hasOwn(ATTACHMENTS_ERROR_LABELS, detail)
    ? ATTACHMENTS_ERROR_LABELS[detail as AttachmentsDetail]
    : DOWNLOAD_FAILED;

// Auth is a Bearer header (D22), so a plain <a href> cannot download: fetch
// the bytes, hand them to the browser through an object URL, then release it.
function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  // In the DOM for the click: Firefox only honours `download` on attached anchors.
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

async function readDetail(res: Response): Promise<unknown> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    return body.detail;
  } catch {
    return undefined;
  }
}

export default function AttachmentsDownload() {
  const [claimRef, setClaimRef] = useState("");
  const [download, setDownload] = useState<Download>({ status: "idle" });
  const busy = download.status === "busy";

  const start = async () => {
    const match = CLAIM_REF_PATTERN.exec(claimRef.trim());
    if (!match) {
      setDownload({
        status: "error",
        message: INVALID_CLAIM_REF,
        settingsHint: false,
      });
      return;
    }
    const [, year, number] = match;
    const filename = `${year}_${number}.zip`;
    setDownload({ status: "busy" });
    try {
      const res = await authFetch(
        apiUrl(`/api/claims/${year}/${number}/attachments`),
      );
      if (!res.ok) {
        const detail = await readDetail(res);
        setDownload({
          status: "error",
          message: errorLabel(detail),
          settingsHint: detail === TRELLO_NO_ACCESS_DETAIL,
        });
        return;
      }
      saveBlob(await res.blob(), filename);
      setDownload({ status: "done", filename });
    } catch {
      setDownload({
        status: "error",
        message: DOWNLOAD_FAILED,
        settingsHint: false,
      });
    }
  };

  return (
    <article>
      <h2>{DOWNLOADS_TITLE}</h2>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void start();
        }}
      >
        <label>
          {CLAIM_REF_LABEL}
          <input
            type="text"
            value={claimRef}
            onChange={(event) => setClaimRef(event.target.value)}
            disabled={busy}
          />
        </label>
        <button type="submit" disabled={busy} aria-busy={busy}>
          {DOWNLOAD}
        </button>
      </form>
      {download.status === "done" && (
        <p>{downloadReadyText(download.filename)}</p>
      )}
      {download.status === "error" && (
        <p role="alert">
          ⚠️ {download.message}
          {download.settingsHint && (
            <>
              {" "}
              <Link to="/settings">{NAV_SETTINGS}</Link>.
            </>
          )}
        </p>
      )}
    </article>
  );
}
