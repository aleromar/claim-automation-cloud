// Central Spanish copy catalog (frontend-spanish REQ-1): every user-visible
// string in the app is a named constant here, grouped by surface. No i18n
// library — Spanish-only frontend (grill, 2026-08-26). Components import
// from this module instead of holding literals; e2e assertions deliberately
// duplicate the same Spanish text (REQ-4.2) rather than importing it, so a
// mis-wire here still fails the e2e suite.

import type { Granularity, Window } from "./metrics";

// Brand — stays English per REQ-3.2 [ASSUMPTION].
export const BRAND = "Claim Automation";

// --- Login / auth ---
export const SIGN_IN_WITH_GOOGLE = "Iniciar sesión con Google";
export const AUTH_ERROR_UNAUTHORIZED = "Esta cuenta no está autorizada.";
export const AUTH_ERROR_GENERIC =
  "El inicio de sesión ha fallado. Inténtalo de nuevo.";

// --- App shell ---
export const CHECKING_SESSION = "Comprobando sesión…";
export const SESSION_CONTRACT_ERROR =
  "La comprobación de sesión devolvió una respuesta inesperada (sin correo de cuenta).";
export const NAV_DASHBOARD = "Panel";
export const NAV_SETTINGS = "Ajustes";
export const LOG_OUT = "Cerrar sesión";
export const CHECKING_BACKEND = "Comprobando el servidor…";
export const BACKEND_UNAVAILABLE = "Servidor no disponible";
// NOT "proceso" — collides with the worker card's own "Proceso" (gate triage).
export const GOOGLE_FLOW_FAILED = "El flujo de Google ha fallado.";

// --- Worker card ---
export const WORKER_TITLE = "Proceso";
export const WORKER_ENABLED_LABEL = "Proceso activado";
export const CHECKING_WORKER = "Comprobando el proceso…";
export const WORKER_STATUS_UNAVAILABLE = "Estado del proceso no disponible";
export const LAST_RUN_PREFIX = "Última ejecución:";
export const NEVER_RAN = "nunca";
export const PROCESS_NOW = "Procesar ahora";
export const RUN_RESULT_PREFIX = "Resultado:";
export const ACTION_FAILED = "La acción ha fallado — estado actualizado";

// The closed outcome set (mirrors backend HeartbeatStatus). Moved here from
// WorkerControls.tsx: the label-map move to strings.ts would otherwise create
// a strings↔WorkerControls import cycle (gate finding). ReconnectBanner
// shares SKIPPED_NO_ACCESS instead of holding its own literal.
export type RunOutcome =
  "ran" | "failed" | "skipped_disabled" | "skipped_no_access" | "skipped_busy";
export const OUTCOME_LABELS: Record<RunOutcome, string> = {
  ran: "completado",
  failed: "fallido",
  skipped_disabled: "omitido (proceso desactivado)",
  skipped_no_access: "omitido (Gmail necesita reconexión)",
  skipped_busy: "omitido (otra ejecución en curso)",
};
export const SKIPPED_NO_ACCESS: RunOutcome = "skipped_no_access";

// Composite phrases — every word lives in this module.
export const countsText = (processed: number, failed: number): string =>
  `${processed} ${processed === 1 ? "procesado" : "procesados"}, ${failed} ${failed === 1 ? "fallido" : "fallidos"}`;
export const failedGaugeText = (gauge: string): string =>
  `${gauge} en estado fallido`;
export const matchingEmailsText = (n: number): string =>
  n === 1 ? "1 correo coincidente" : `${n} correos coincidentes`;

// --- Reconnect banner ---
export const RECONNECT_BANNER_TEXT =
  "El acceso a Gmail ha caducado: la última ejecución no pudo autenticarse.";
export const RECONNECT_GMAIL = "Reconectar Gmail";

// --- Metrics ---
export const METRICS_TITLE = "Métricas";
export const LOADING_METRICS = "Cargando métricas…";
export const METRICS_UNAVAILABLE = "Métricas no disponibles";
export const TILE_EMAILS_PROCESSED = "Correos procesados";
export const TILE_CARDS_CREATED = "Tarjetas creadas";
export const TILE_EMAILS_FAILED = "Correos fallidos";
export const TILE_FAILED_RUNS = "Ejecuciones fallidas";
export const TIME_WINDOW_LABEL = "Periodo";
export const GRANULARITY_LABEL = "Granularidad";
export const WINDOW_LABELS: Record<Window, string> = {
  "1d": "1 día",
  "7d": "7 días",
  "30d": "30 días",
  "90d": "90 días",
  "1y": "1 año",
  all: "Todo",
};
export const GRANULARITY_LABELS: Record<Granularity, string> = {
  hour: "Por hora",
  day: "Por día",
  week: "Por semana",
  month: "Por mes",
};
export const CHART_ARIA_LABEL = "Siniestros y errores por intervalo";
export const SERIES_CLAIMS = "Siniestros";
// "Errores", not "Correos fallidos": the tile label would otherwise be
// byte-identical to the legend/tooltips (Bugfix log — translation collision).
export const SERIES_ERRORS = "Errores";
export const TABLE_DATE = "Fecha";
export const TABLE_CLAIM = "Siniestro";
export const TABLE_TYPE = "Tipo";
export const TABLE_TOWN = "Población";
export const TABLE_OWNER = "Titular";
export const NO_CLAIMS_IN_WINDOW = "No hay siniestros en este periodo.";

// --- Settings ---
export const SETTINGS_TITLE = "Ajustes";
export const LOADING_SETTINGS = "Cargando ajustes…";
export const SETTINGS_UNAVAILABLE = "Ajustes no disponibles";
export const API_KEY_LABEL = "Clave API";
export const API_TOKEN_LABEL = "Token API";
export const STORED_BADGE = "(guardado)";
export const NOT_SET_BADGE = "(sin configurar)";
export const PLACEHOLDER_KEEP_KEY =
  "dejar en blanco para conservar la clave guardada";
export const PLACEHOLDER_KEEP_TOKEN =
  "dejar en blanco para conservar el token guardado";
export const BOARD_ID_LABEL = "ID del tablero";
export const LIST_ID_LABEL = "ID de la lista";
export const SAVE_TRELLO = "Guardar ajustes de Trello";
export const SAVE_FAILED =
  "Error al guardar: no se ha perdido nada y es seguro reintentar.";
export const ACCOUNT_LABEL = "Cuenta:";
export const REFRESH_TOKEN_LABEL = "Token de actualización:";
export const TOKEN_STORED = "guardado";
export const TOKEN_NOT_SET = "sin configurar";
