// Deliberately excludes identity, filters, request URLs, headers and upstream text.
export const readEndpoints = ["getVisibleChains", "getCategories", "getProducts", "getPeriods", "getHistoricalObservations"] as const;
export const readTables = ["chains", "categories", "products", "import_profile_versions", "portal_monthly_observations_current", "portal_history_periods"] as const;
export const readOperations = ["select", "head_count", "catalog_page", "period_bounds", "period_count", "period_catalog", "page_1", "cursor_page", "combine_metrics", "catalog_filter", "complete"] as const;
export type PortalReadDiagnostic = {
  endpoint: typeof readEndpoints[number];
  table: typeof readTables[number];
  http_status: number;
  code: string;
  operation: typeof readOperations[number];
};
const sqlCodes = ["42501", "42703", "42P01", "57014", "22007", "22P02", "23505", "23503", "23514", "P0001"];
const localCodes = ["OK", "READ_FAILED", "NETWORK_ERROR", "DUPLICATE_CURRENT_METRIC", "INVALID_OBSERVATION_VALUE", "INVALID_PERIOD", "TRANSFORM_FAILED"];
export function controlledReadCode(code: unknown): string {
  return typeof code === "string" && (/^PGRST\d{3}$/.test(code) || sqlCodes.includes(code) || localCodes.includes(code)) ? code : "READ_FAILED";
}
export function parseReadDiagnostic(value: unknown): PortalReadDiagnostic | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const d = value as Record<string, unknown>;
  const keys = ["endpoint", "table", "http_status", "code", "operation"];
  if (Object.keys(d).length !== keys.length || !keys.every(k => k in d)) return null;
  if (!readEndpoints.includes(d.endpoint as PortalReadDiagnostic["endpoint"]) || !readTables.includes(d.table as PortalReadDiagnostic["table"]) || !readOperations.includes(d.operation as PortalReadDiagnostic["operation"])) return null;
  if (typeof d.http_status !== "number" || !Number.isInteger(d.http_status) || d.http_status < 0 || d.http_status > 599 || typeof d.code !== "string" || controlledReadCode(d.code) !== d.code) return null;
  return { endpoint: d.endpoint as PortalReadDiagnostic["endpoint"], table: d.table as PortalReadDiagnostic["table"], http_status: d.http_status, code: d.code, operation: d.operation as PortalReadDiagnostic["operation"] };
}
export function logReadDiagnostic(diagnostic: PortalReadDiagnostic) {
  const safe = parseReadDiagnostic(diagnostic);
  if (safe) console.info(`[TowellPortalRead] ${JSON.stringify(safe)}`);
}
export class PortalReadError extends Error {
  constructor(readonly diagnostic: PortalReadDiagnostic) { super("read_failed"); this.name = "PortalReadError"; }
}
