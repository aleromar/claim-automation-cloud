// Seeds the Azurite `membretes` blob container with a synthetic letterhead
// (D26: real membretes render personal data and never enter this repo; the
// declaración PDF path raises without `normal.png` — pdf_gen.py C6).

// NOTE: Playwright boots the webServers BEFORE globalSetup runs (verified in
// the installed runner, Gate 3 #9) — the real fail-fast for missing env is
// requireLiveEnv() at config load, which precedes both.

import { BlobServiceClient } from "@azure/storage-blob";

// 60×8 white PNG, same shape the backend unit fixtures synthesize.
const SYNTHETIC_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAADwAAAAICAIAAAAqdQueAAAAKElEQVR4nNXOQREAIADDsFL/" +
    "nocHPlyjIGcbNRIkQRIkQRIkQf4OvLgU9QMNQcTPKwAAAABJRU5ErkJggg==",
  "base64",
);

export default async function globalSetup(): Promise<void> {
  const container = BlobServiceClient.fromConnectionString(
    "UseDevelopmentStorage=true",
  ).getContainerClient("membretes");
  await container.createIfNotExists();
  // The full MEMBRETE_BY_TYPE set (pdf_gen.py): declaración, urgente, asistencia.
  for (const name of ["normal.png", "urgente.png", "asistencia.png"]) {
    await container.getBlockBlobClient(name).uploadData(SYNTHETIC_PNG, {
      blobHTTPHeaders: { blobContentType: "image/png" },
    });
  }
}
