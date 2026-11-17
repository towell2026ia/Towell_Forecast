import type { OperationalReadContext } from "./supabase/types";

export const salesUnavailableMessage = "Sin cálculo: fuente de ventas no disponible";
export const sourceMismatchMessage = "La fuente operativa no coincide con la fuente de ventas autorizada. Actualiza la consulta.";
const fields = ["sales_source", "inventory_source", "sales_available", "sales_contract", "sales_file_sha256", "sales_sheet", "authorized_source_hash"];
export function salesSourceState(context?: Partial<OperationalReadContext> | null): "AVAILABLE" | "UNAVAILABLE" | "INVALID" {
  // Hosted readers and their established fixtures retain their original contract.
  // Any supplied source-contract metadata must satisfy the complete local contract.
  if (!context || !fields.some(field => field in context)) return "AVAILABLE";
  if (context.sales_source !== "TOWELL" || context.inventory_source !== "ESTADISTICA" || context.sales_contract !== "towell-sales-v1"
    || context.sales_sheet !== "Towell" || typeof context.sales_available !== "boolean"
    || !/^[0-9a-f]{64}$/.test(context.sales_file_sha256 ?? "") || context.authorized_source_hash !== context.source_hash) return "INVALID";
  return context.sales_available ? "AVAILABLE" : "UNAVAILABLE";
}
export function salesSourceAvailable(context?: Partial<OperationalReadContext> | null) {
  return salesSourceState(context) === "AVAILABLE";
}
export function officialSourceKey(context?: Partial<OperationalReadContext> | null, errorCode?: string) {
  return context ? JSON.stringify([context.source_hash, context.generation, context.sales_contract ?? null, context.sales_file_sha256 ?? null,
    context.authorized_source_hash ?? null, context.sales_available ?? null]) : sourceReadMessage(errorCode) ?? "SOURCE_NOT_RESOLVED";
}
export function sourceReadMessage(code?: string) {
  if (code === "SALES_SOURCE_UNAVAILABLE" || code === "sales_source_unavailable") return salesUnavailableMessage;
  if (["OFFICIAL_SOURCE_MISMATCH", "official_source_mismatch", "official_source_changed", "OFFICIAL_SOURCE_CHANGED"].includes(code ?? "")) return sourceMismatchMessage;
  return null;
}
