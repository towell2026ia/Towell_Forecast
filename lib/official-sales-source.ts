import type { OperationalReadContext } from "./supabase/types";

export const salesUnavailableMessage = "Sin cálculo: fuente de ventas no disponible";
export const sourceMismatchMessage = "La fuente operativa no coincide con la fuente de ventas autorizada. Actualiza la consulta.";
const fields = ["sales_source", "inventory_source", "sales_available", "sales_contract", "sales_file_sha256", "sales_sheet", "authorized_source_hash", "sales_training_cutoff"];
// Same accredited identity fields as SupabaseReadClient.SALES_SOURCE_FIELDS.
export const salesSourceIdentityFields = ["source_hash", "generation", "first_period", "last_period", "sales_contract", "sales_source", "sales_sheet", "sales_file_sha256", "sales_training_cutoff", "authorized_source_hash", "inventory_source"] as const;
const month = /^20\d{2}-(0[1-9]|1[0-2])$/;
const orderFields = ["forecast_contract", "objective", "target_metric", "target_sheet", "target_file_sha256", "training_cutoff", "forecast_available", "target_source_available", "forecast_block_code", "forecast_unavailable_reason", "unit"] as const;
export const orderSourceIdentityFields = ["source_hash", "generation", "first_period", "last_period", "forecast_contract", "objective", "target_metric", "target_sheet", "target_file_sha256", "training_cutoff", "unit", "authorized_source_hash", "inventory_source", "target_source_available", "forecast_block_code", "fulfillment_source", "fulfillment_file_sha256", "fulfillment_available", "planning_start", "planning_end"] as const;
export const unifiedOrderContract = "pedido-unificado-v1";
export const unifiedMasterSha = "29cac8aebab869e1ded099fa93382046bdbafbaaf4c0156664d52c9488a47c5d";
const scenarioIdentityFields = ["planning_basis", "observed_cutoff", "scenario_hash", "fulfillment_mode", "fulfillment_report_sha256"] as const;
const scenarioOrderIdentityFields = [...orderSourceIdentityFields, ...scenarioIdentityFields] as const;
export type ForecastObjective = "Venta" | "Pedido";
export function validFulfillmentSource(context: Partial<OperationalReadContext>) {
  if (!/^[0-9a-f]{64}$/.test(context.fulfillment_file_sha256 ?? "") || context.fulfillment_available !== true) return false;
  if (context.fulfillment_source === "ESTADISTICA_BAASE_REPORTED") {
    return context.planning_basis === "OBSERVED_AND_COMMERCIAL_CLOSE" && context.fulfillment_mode === "REPORTED_PERCENTAGE"
      && /^[0-9a-f]{64}$/.test(context.fulfillment_report_sha256 ?? "");
  }
  return context.fulfillment_source === "ESTADISTICA_BAASE" && !context.fulfillment_mode && !context.fulfillment_report_sha256;
}
export function isOrderSource(context?: Partial<OperationalReadContext> | null) { return Boolean(context && orderFields.some(field => field in context)); }
export function forecastSourceState(context?: Partial<OperationalReadContext> | null): "AVAILABLE" | "UNAVAILABLE" | "INVALID" {
  if (!isOrderSource(context)) return salesSourceState(context);
  if (context && ["fulfillment_source", "fulfillment_file_sha256", "fulfillment_available"].some(field => field in context)
    && !validFulfillmentSource(context)) return "INVALID";
  const unclosed = context?.training_cutoff === null && context.forecast_available === false && context.target_source_available === true && context.forecast_block_code === "ORDER_CLOSURE_UNVERIFIED";
  const missing = context?.target_source_available === false && context.forecast_available === false && context.training_cutoff === null;
  const unified = context?.forecast_contract === unifiedOrderContract;
  if (unified) {
    if (context.target_file_sha256 !== unifiedMasterSha || context.planning_start !== "2027-01" || context.planning_end !== "2027-12") return "INVALID";
    const scenario = scenarioIdentityFields.some(field => field in context) || (context.generation ?? 0) >= 6;
    if (scenario) {
      if (context.planning_basis !== "OBSERVED_AND_COMMERCIAL_CLOSE" || (context.generation ?? 0) < 6
        || context.training_cutoff !== "2026-12" || context.observed_cutoff !== "2026-09"
        || !/^[0-9a-f]{64}$/.test(context.scenario_hash ?? "")) return "INVALID";
    } else if (context.training_cutoff !== "2026-09") return "INVALID";
  } else if (context && scenarioIdentityFields.some(field => field in context)) return "INVALID";
  if (!context || (!unified && context.forecast_contract !== "pedido-towell-v1") || context.objective !== "Pedido" || context.target_metric !== "ORDER"
    || context.target_sheet !== (unified ? "Base_2024_2026" : "Pedidos") || context.unit !== "PIEZAS" || context.inventory_source !== "ESTADISTICA" || typeof context.forecast_available !== "boolean" || typeof context.target_source_available !== "boolean"
    || context.forecast_available && (!context.target_source_available || context.forecast_block_code != null)
    || !Number.isSafeInteger(context.generation) || context.generation! < 1 || !/^[0-9a-f]{64}$/.test(context.source_hash ?? "")
    || !/^[0-9a-f]{64}$/.test(context.target_file_sha256 ?? "") || context.authorized_source_hash !== context.source_hash
    || !month.test(context.first_period ?? "") || !month.test(context.last_period ?? "") || context.first_period! > context.last_period!
    || !unclosed && !missing && (!month.test(context.training_cutoff ?? "") || !(context.first_period! <= context.training_cutoff! && context.training_cutoff! <= context.last_period!))) return "INVALID";
  return context.forecast_available ? "AVAILABLE" : "UNAVAILABLE";
}
export function forecastSourceAvailable(context?: Partial<OperationalReadContext> | null) { return forecastSourceState(context) === "AVAILABLE"; }
export function forecastHistoryAvailable(context?: Partial<OperationalReadContext> | null) {
  const state = forecastSourceState(context);
  return state === "AVAILABLE" || state === "UNAVAILABLE" && isOrderSource(context) && context?.target_source_available === true
    && (context.forecast_block_code === "ORDER_CLOSURE_UNVERIFIED" || context.forecast_block_code === "PLANNING_SOURCE_UNAVAILABLE");
}
export function forecastObjective(context?: Partial<OperationalReadContext> | null): ForecastObjective | null {
  return forecastSourceState(context) === "INVALID" ? null : isOrderSource(context) ? "Pedido" : "Venta";
}
export function forecastTrainingCutoff(context?: Partial<OperationalReadContext> | null) {
  return forecastSourceState(context) === "INVALID" ? null : isOrderSource(context) ? context?.training_cutoff ?? null : context?.sales_training_cutoff ?? context?.last_period ?? null;
}
export function forecastUnavailableMessage(context?: Partial<OperationalReadContext> | null) {
  if (isOrderSource(context) && context?.forecast_block_code === "ORDER_CLOSURE_UNVERIFIED") return "El cierre de Pedidos aún no está acreditado.";
  return isOrderSource(context) ? "Sin cálculo: fuente de pedidos no disponible" : salesUnavailableMessage;
}
export function forecastSourceIdentityFields(context?: Partial<OperationalReadContext> | null) {
  if (context?.planning_basis) return scenarioOrderIdentityFields;
  return isOrderSource(context) ? orderSourceIdentityFields : salesSourceIdentityFields;
}
export function salesSourceState(context?: Partial<OperationalReadContext> | null): "AVAILABLE" | "UNAVAILABLE" | "INVALID" {
  // Hosted readers and their established fixtures retain their original contract.
  // Any supplied source-contract metadata must satisfy the complete local contract.
  if (!context || !fields.some(field => field in context)) return "AVAILABLE";
  if (context.sales_source !== "TOWELL" || context.inventory_source !== "ESTADISTICA" || context.sales_contract !== "towell-sales-v1"
    || context.sales_sheet !== "Towell" || typeof context.sales_available !== "boolean"
    || !Number.isSafeInteger(context.generation) || context.generation! < 1
    || !/^[0-9a-f]{64}$/.test(context.source_hash ?? "") || !/^[0-9a-f]{64}$/.test(context.sales_file_sha256 ?? "")
    || context.authorized_source_hash !== context.source_hash
    || !month.test(context.first_period ?? "") || !month.test(context.last_period ?? "") || !month.test(context.sales_training_cutoff ?? "")
    || !(context.first_period! <= context.sales_training_cutoff! && context.sales_training_cutoff! <= context.last_period!)) return "INVALID";
  return context.sales_available ? "AVAILABLE" : "UNAVAILABLE";
}
export function salesSourceAvailable(context?: Partial<OperationalReadContext> | null) {
  return salesSourceState(context) === "AVAILABLE";
}
export function officialSourceKey(context?: Partial<OperationalReadContext> | null, errorCode?: string) {
  return context ? JSON.stringify([...forecastSourceIdentityFields(context).map(field => context[field] ?? null), isOrderSource(context) ? context.forecast_available ?? null : context.sales_available ?? null])
    : sourceReadMessage(errorCode) ?? "SOURCE_NOT_RESOLVED";
}
export function sourceReadMessage(code?: string) {
  if (code === "SALES_SOURCE_UNAVAILABLE" || code === "sales_source_unavailable") return salesUnavailableMessage;
  if (code === "FORECAST_SOURCE_UNAVAILABLE" || code === "ORDER_SOURCE_UNAVAILABLE" || code === "PLANNING_SOURCE_UNAVAILABLE") return "Sin cálculo: fuente de pedidos no disponible";
  if (code === "ORDER_CLOSURE_UNVERIFIED") return "El cierre de Pedidos aún no está acreditado.";
  if (["OFFICIAL_SOURCE_MISMATCH", "official_source_mismatch", "official_source_changed", "OFFICIAL_SOURCE_CHANGED"].includes(code ?? "")) return sourceMismatchMessage;
  return null;
}
