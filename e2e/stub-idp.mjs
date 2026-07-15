// Stub IdP standing in for Google in e2e (REQ-6.3) — the ONE documented
// exception to "E2E uses no mocks" (Google blocks automated sign-in).
// Replicates: state echo, single-use codes, id_token issuance, an
// omitted-refresh-token variant, and the access_denied path.
import { createHmac, randomUUID } from "node:crypto";
import { createServer } from "node:http";

const PORT = 9100;
const OPERATOR = "operator@example.com";
const codes = new Map();

const b64url = (value) => Buffer.from(value).toString("base64url");

function signIdToken(aud) {
  const now = Math.floor(Date.now() / 1000);
  const header = b64url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const payload = b64url(
    JSON.stringify({
      iss: "https://accounts.google.com",
      aud,
      email: OPERATOR,
      iat: now,
      exp: now + 300,
    }),
  );
  const signature = createHmac("sha256", "stub-idp-key")
    .update(`${header}.${payload}`)
    .digest("base64url");
  return `${header}.${payload}.${signature}`;
}

createServer((req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);

  if (url.pathname === "/healthz") {
    res.end("ok");
    return;
  }

  if (url.pathname === "/authorize") {
    const redirect = url.searchParams.get("redirect_uri");
    const state = encodeURIComponent(url.searchParams.get("state") ?? "");
    const aud = url.searchParams.get("client_id");
    const issueCode = (norefresh) => {
      const code = randomUUID();
      codes.set(code, { aud, norefresh, used: false });
      return code;
    };
    res.setHeader("content-type", "text/html");
    res.end(`<h1>Stub IdP consent</h1>
      <a href="${redirect}?code=${issueCode(false)}&state=${state}">Approve</a>
      <a href="${redirect}?code=${issueCode(true)}&state=${state}">Approve without refresh token</a>
      <a href="${redirect}?error=access_denied&state=${state}">Deny</a>`);
    return;
  }

  if (url.pathname === "/token" && req.method === "POST") {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", () => {
      const record = codes.get(new URLSearchParams(body).get("code"));
      if (!record || record.used) {
        res.statusCode = 400; // single-use codes, like Google
        res.end("{}");
        return;
      }
      record.used = true;
      const payload = { id_token: signIdToken(record.aud) };
      if (!record.norefresh) payload.refresh_token = `rt-${randomUUID()}`;
      res.setHeader("content-type", "application/json");
      res.end(JSON.stringify(payload));
    });
    return;
  }

  res.statusCode = 404;
  res.end();
}).listen(PORT);
