import type { OperationalReadContext } from "./supabase/types";
import { forecastSourceState } from "./official-sales-source";

/** Explicit transport selection. Missing hosted objects are errors, never legacy fallbacks. */
export function hostedG6Enabled() {
  return process.env.NEXT_PUBLIC_HOSTED_G6_ENABLED === "true";
}

const hostedTables = {
  products: "ft_g6_products", categories: "ft_g6_categories", chains: "ft_g6_chains",
  import_profile_versions: "ft_g6_import_profile_versions",
  forecast_calculations: "ft_g6_forecast_calculations",
  forecast_calculation_horizons: "ft_g6_forecast_calculation_horizons",
} as const;

export function operationalTable(table: string) {
  return hostedG6Enabled() && table in hostedTables ? hostedTables[table as keyof typeof hostedTables] : table;
}

export function assertHostedG6Context(context: OperationalReadContext) {
  if (hostedG6Enabled() && (context.generation < 6 || context.forecast_contract !== "pedido-unificado-v1"
    || context.objective !== "Pedido" || context.target_metric !== "ORDER" || context.target_sheet !== "Base_2024_2026"
    || context.planning_basis !== "OBSERVED_AND_COMMERCIAL_CLOSE" || forecastSourceState(context) !== "AVAILABLE"))
    throw new Error("OFFICIAL_SOURCE_MISMATCH");
}

export function championPublicationEnabled(capabilities?: { local_champion_publication?: boolean; hosted_champion_publication?: boolean } | null) {
  return hostedG6Enabled() ? capabilities?.hosted_champion_publication === true : capabilities?.local_champion_publication === true;
}

export function championPublicationOrigin(record: { environment?: string; publication_scope?: string }) {
  return hostedG6Enabled() ? record.environment === "hosted" && record.publication_scope === "HOSTED_OPERATIONAL"
    : record.environment === "localhost" && record.publication_scope === "LOCAL_OPERATIONAL";
}
