import type { ChampionFamily, LocalChampions, LocalChampionQuery } from "./forecast-preview";
import { chooseCalculations, emptyReportChoice, matchesOfficialSource, type ReportSnapshot } from "./forecast-reporting";
import { forecastSourceState } from "./official-sales-source";

export const championFamilies: ChampionFamily[] = ["STATISTICAL", "ML", "ENSEMBLE", "COMMERCIAL_PLAN"];
export const championFamilyNames: Record<ChampionFamily, string> = { STATISTICAL: "Estadístico", ML: "Machine Learning", ENSEMBLE: "Ensemble", COMMERCIAL_PLAN: "Plan comercial" };
export type ChampionSelection = LocalChampionQuery & { run_id: string; eligible: Record<ChampionFamily, string[]>; productChains: Record<string, string> };

/** Publication evidence stays separate from the frozen E4 decision and input. */
export function verifiedLocalChampions(snapshot: ReportSnapshot, result: LocalChampions): LocalChampions {
  const source = snapshot.sourceContext;
  const allowed = new Map(snapshot.products.map(product => [product.id, product.chain_id]));
  const sha = (value: unknown) => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
  if (!source || source.forecast_contract !== "pedido-unificado-v1" || forecastSourceState(source) !== "AVAILABLE"
    || !matchesOfficialSource(result.source_context, source) || source.planning_start !== `${result.planning_year}-01`
    || source.planning_end !== `${result.planning_year}-12` || result.eligible !== allowed.size
    || !Array.isArray(result.products) || result.covered !== result.products.length
    || new Set(result.products.map(row => row.product?.product_id)).size !== result.products.length) throw new Error("champion_source_lineage");
  const byRun = new Map(snapshot.calculations.map(calculation => [`${calculation.product_id}/${calculation.input_snapshot.planning_run_id}`, calculation]));
  const byPeriod = new Map(snapshot.horizons.map(row => [`${row.calculation_id}/${row.horizon}`, row]));
  for (const row of result.products) {
    const product = row.product;
    if (!row.id || !row.run_id || row.objective !== "Pedido" || row.environment !== "localhost" || row.status !== "ACTIVE"
      || row.unit !== "PIEZAS" || row.cutoff !== source.training_cutoff || row.planning_year !== result.planning_year
      || !sha(row.run_content_digest) || !sha(row.run_file_sha256) || !row.selected_by
      || !Number.isFinite(Date.parse(row.selected_at)) || !Number.isFinite(Date.parse(row.run_emitted_at))
      || !matchesOfficialSource(row.source_context, source) || allowed.get(product?.product_id) !== product?.chain_id
      || !championFamilies.includes(row.candidate_family) || product.candidate_family !== row.candidate_family || !product.model || !product.origin
      || !Array.isArray(product.horizons) || product.horizons.length !== 12) throw new Error("champion_source_lineage");
    const calculation = byRun.get(`${product.product_id}/${row.run_id}`);
    for (const [index, h] of product.horizons.entries()) {
      const month = index + 1, distance = (result.planning_year - Number(row.cutoff.slice(0, 4))) * 12 + month - Number(row.cutoff.slice(5, 7));
      if (h.horizon !== month || h.planning_horizon !== month || h.forecast_distance !== distance
        || h.target_period !== `${result.planning_year}-${String(month).padStart(2, "0")}`
        || typeof h.value !== "string" || !/^\d+(?:\.\d+)?$/.test(h.value) || !Number.isFinite(Number(h.value))) throw new Error("champion_horizon_lineage");
      if (calculation) {
        const horizon = byPeriod.get(`${calculation.id}/${month}`);
        const value = row.candidate_family === "COMMERCIAL_PLAN"
          ? calculation.candidate_metrics.COMMERCIAL_PLAN?.horizons.find(candidate => candidate.horizon === month && candidate.target_period.slice(0, 7) === h.target_period)?.value
          : horizon?.[row.candidate_family === "STATISTICAL" ? "statistical_value" : row.candidate_family === "ML" ? "ml_value" : "ensemble_value"];
        if (value == null || value !== Number(h.value) || row.candidate_family !== "COMMERCIAL_PLAN" && horizon?.target_period.slice(0, 7) !== h.target_period)
          throw new Error("champion_horizon_lineage");
      }
    }
  }
  return result;
}

/** UI eligibility is only a hint; publication resolves amounts from the sealed run server-side. */
export function localChampionSelection(snapshot: ReportSnapshot): ChampionSelection | null {
  const source = snapshot.sourceContext;
  if (!source || source.forecast_contract !== "pedido-unificado-v1" || forecastSourceState(source) !== "AVAILABLE") return null;
  const year = Number(source.planning_start?.slice(0, 4));
  if (!Number.isSafeInteger(year) || source.planning_start !== `${year}-01` || source.planning_end !== `${year}-12`) return null;
  const products = [...new Map(snapshot.products.map(p => [p.id, p])).values()];
  if (!products.length) return null;
  const calculations = chooseCalculations(snapshot, emptyReportChoice);
  const eligible: ChampionSelection["eligible"] = { STATISTICAL: [], ML: [], ENSEMBLE: [], COMMERCIAL_PLAN: [] };
  const runs = new Set<string>();
  const rows = new Map<string, typeof snapshot.horizons>();
  for (const h of snapshot.horizons) rows.set(h.calculation_id, [...(rows.get(h.calculation_id) ?? []), h]);
  for (const product of products) {
    const calculation = calculations.get(`${product.chain_id}/${product.id}`);
    const run = calculation?.input_snapshot.planning_run_id;
    const allowed = calculation?.input_snapshot.champion_eligible;
    if (!calculation || typeof run !== "string" || !/^[0-9a-f-]{36}$/i.test(run) || !Array.isArray(allowed)
      || !matchesOfficialSource(calculation.input_snapshot.source_context, source)) continue;
    const horizons = rows.get(calculation.id) ?? [];
    let valid = false;
    for (const family of championFamilies) {
      if (!allowed.includes(family)) continue;
      const candidate = calculation.candidate_metrics[family];
      const curve = family === "COMMERCIAL_PLAN" ? candidate?.horizons : horizons.map(h => ({ horizon: h.horizon, target_period: h.target_period,
        value: h[family === "STATISTICAL" ? "statistical_value" : family === "ML" ? "ml_value" : "ensemble_value"] }));
      if (!Array.isArray(curve) || curve.length !== 12 || new Set(curve.map(h => h.horizon)).size !== 12
        || curve.some(h => !Number.isSafeInteger(h.horizon) || h.horizon < 1 || h.horizon > 12 || typeof h.target_period !== "string"
          || h.target_period.slice(0, 7) !== `${year}-${String(h.horizon).padStart(2, "0")}`
          || typeof h.value !== "number" || !Number.isFinite(h.value) || h.value < 0)) continue;
      eligible[family].push(product.id); valid = true;
    }
    if (valid) runs.add(run);
  }
  if (runs.size !== 1) return null;
  return { source_context: source, planning_year: year, run_id: [...runs][0],
    chain_ids: [...new Set(products.map(p => p.chain_id))].sort(), product_ids: products.map(p => p.id).sort(),
    productChains: Object.fromEntries(products.map(p => [p.id, p.chain_id])), eligible };
}
