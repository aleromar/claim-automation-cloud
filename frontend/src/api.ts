// Deployment REQ-4.2: one place decides where the API lives.
// Dev: VITE_API_BASE_URL unset -> relative path, Vite proxies /api to the backend.
// Prod: set at build time to the Function App origin (SWA and Functions are
// cross-origin — D22), so every call site stays origin-agnostic.

export function apiUrl(path: string): string {
  const base = (import.meta.env.VITE_API_BASE_URL ?? "") as string;
  return base.replace(/\/$/, "") + path;
}
