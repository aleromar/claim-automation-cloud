// Session token handling (REQ-4): fragment consumption, sessionStorage, Bearer fetch.

const TOKEN_KEY = "session_jwt";

/**
 * Move a `#token=…` fragment into sessionStorage, or surface a `#error=…` code.
 * Must run before first render so the token never survives in the URL/history.
 */
export function consumeFragment(): { error: string | null } {
  const hash = window.location.hash;
  let error: string | null = null;
  if (hash.startsWith("#token=")) {
    sessionStorage.setItem(TOKEN_KEY, hash.slice("#token=".length));
  } else if (hash.startsWith("#error=")) {
    error = hash.slice("#error=".length);
  } else {
    return { error: null };
  }
  history.replaceState(
    null,
    "",
    window.location.pathname + window.location.search,
  );
  return { error };
}

export function getToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY);
}

export function clearToken(): void {
  sessionStorage.removeItem(TOKEN_KEY);
}

/** fetch with the Bearer header attached; a 401 clears the stored token (REQ-4.3). */
export async function authFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const token = getToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(input, { ...init, headers });
  if (res.status === 401) clearToken();
  return res;
}
