/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Function App origin in prod; unset in dev (Vite proxy handles /api). */
  readonly VITE_API_BASE_URL?: string;
  /** Git SHA stamped by deploy-swa.yml at build time; unset in dev/CI. */
  readonly VITE_BUILD_VERSION?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
