/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Function App origin in prod; unset in dev (Vite proxy handles /api). */
  readonly VITE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
