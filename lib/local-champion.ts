import type { ChampionFamily, LocalChampions, LocalChampionQuery } from "./forecast-preview";
import { addReportMonth, chooseCalculations, emptyReportChoice, matchesOfficialSource, type ReportSnapshot } from "./forecast-reporting";
import { forecastSourceState } from "./official-sales-source";
import { championPublicationOrigin } from "./hosted-g6";

export const championFamilies: ChampionFamily[] = ["STATISTICAL", "ML", "ENSEMBLE", "COMMERCIAL_PLAN", "COLD_START"];
export const championFamilyNames: Record<ChampionFamily, string> = { STATISTICAL: "Estadístico", ML: "Machine Learning", ENSEMBLE: "Ensemble", COMMERCIAL_PLAN: "Plan comercial", COLD_START: "Cold Start" };
export type ChampionSelection = LocalChampionQuery & { run_id: string; eligible: Record<Exclude<ChampionFamily, "COLD_START">, string[]> & { COLD_START?: string[] }; productChains: Record<string, string>; productNames?: Record<string, string> };

function championWindow(snapshot: ReportSnapshot) {
  const source = snapshot.sourceContext, run = snapshot.planningRun;
  const start = run?.window_start ?? source?.planning_start, end = run?.window_end ?? source?.planning_end;
  if (!start || !end || !/^20\d{2}-(0[1-9]|1[0-2])$/.test(start) || addReportMonth(start, 11) !== end) return null;
  if (run && (run.status !== "READY" || run.source_hash !== source?.source_hash || run.generation !== source?.generation
    || addReportMonth(run.issue_period, 1) !== start)) return null;
  return { start, end, year: Number(start.slice(0, 4)), cutoff: run?.issue_period ?? source?.training_cutoff };
}
function coldAdmission(value: unknown) {
  if (!value || typeof value !== "object") return false;
  const admission = (value as Record<string, unknown>).admission;
  return Boolean(admission && typeof admission === "object" && (admission as Record<string, unknown>).manual_selection_allowed === true
    && ((admission as Record<string, unknown>).status === "REQUIRES_EXPLICIT_ACCEPTANCE"
      || (admission as Record<string, unknown>).requires_explicit_acceptance === true));
}

/** Publication evidence stays separate from the frozen E4 decision and input. */
export function verifiedLocalChampions(snapshot: ReportSnapshot, result: LocalChampions): LocalChampions {
  const source = snapshot.sourceContext;
  const window = championWindow(snapshot);
  const allowed = new Map(snapshot.products.map(product => [product.id, product.chain_id]));
  const sha = (value: unknown) => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
  if (!source || source.forecast_contract !== "pedido-unificado-v1" || forecastSourceState(source) !== "AVAILABLE"
    || !window || !matchesOfficialSource(result.source_context, source) || result.planning_year !== window.year
    || (snapshot.planningRun && (result.window_start !== window.start || result.window_end !== window.end || result.run_id !== snapshot.planningRun.run_id))
    || result.eligible !== allowed.size
    || !Array.isArray(result.products) || result.covered !== result.products.length
    || new Set(result.products.map(row => row.product?.product_id)).size !== result.products.length) throw new Error("champion_source_lineage");
  const byRun = new Map(snapshot.calculations.map(calculation => [`${calculation.product_id}/${calculation.input_snapshot.planning_run_id}`, calculation]));
  const byPeriod = new Map(snapshot.horizons.map(row => [`${row.calculation_id}/${row.horizon}`, row]));
  for (const row of result.products) {
    const product = row.product;
    if (!row.id || !row.run_id || row.objective !== "Pedido" || !championPublicationOrigin(row) || row.status !== "ACTIVE"
      || row.official_publication !== false
      || row.unit !== "PIEZAS" || row.cutoff !== window.cutoff || row.planning_year !== result.planning_year
      || (snapshot.planningRun && (row.run_id !== snapshot.planningRun.run_id || row.window_start !== window.start || row.window_end !== window.end))
      || !sha(row.run_content_digest) || !sha(row.run_file_sha256) || !row.selected_by
      || !Number.isFinite(Date.parse(row.selected_at)) || !Number.isFinite(Date.parse(row.run_emitted_at))
      || !matchesOfficialSource(row.source_context, source) || allowed.get(product?.product_id) !== product?.chain_id
      || !championFamilies.includes(row.candidate_family) || product.candidate_family !== row.candidate_family || !product.model || !product.origin
      || !Array.isArray(product.horizons) || product.horizons.length !== 12) throw new Error("champion_source_lineage");
    const calculation = byRun.get(`${product.product_id}/${row.run_id}`);
    const cold = row.candidate_family === "COLD_START" ? snapshot.provisionals.find(p => p.product_id === product.product_id
      && p.chain_id === product.chain_id && p.job_id === row.run_id) : null;
    if (row.candidate_family === "COLD_START" && (!snapshot.planningRun || !cold || !coldAdmission(cold)
      || !matchesOfficialSource(cold.source_context, source) || cold.model !== product.model)) throw new Error("champion_source_lineage");
    for (const [index, h] of product.horizons.entries()) {
      const month = index + 1, target = addReportMonth(window.start, index);
      const distance = (Number(target.slice(0, 4)) - Number(row.cutoff.slice(0, 4))) * 12 + Number(target.slice(5, 7)) - Number(row.cutoff.slice(5, 7));
      if (h.horizon !== month || h.planning_horizon !== month || h.forecast_distance !== distance
        || h.target_period !== target
        || typeof h.value !== "string" || !/^\d+(?:\.\d+)?$/.test(h.value) || !Number.isFinite(Number(h.value))) throw new Error("champion_horizon_lineage");
      if (cold) {
        const original = cold.horizons.find(candidate => candidate.horizon === month && candidate.target_period.slice(0, 7) === target);
        if (original?.value == null || Number(original.value) !== Number(h.value)) throw new Error("champion_horizon_lineage");
      } else if (calculation) {
        const horizon = byPeriod.get(`${calculation.id}/${month}`);
        const candidateCurve = row.candidate_family === "COMMERCIAL_PLAN" || row.candidate_family === "COLD_START";
        const value = candidateCurve
          ? calculation.candidate_metrics[row.candidate_family]?.horizons.find(candidate => candidate.horizon === month && candidate.target_period.slice(0, 7) === h.target_period)?.value
          : horizon?.[row.candidate_family === "STATISTICAL" ? "statistical_value" : row.candidate_family === "ML" ? "ml_value" : "ensemble_value"];
        if (value == null || Number(value) !== Number(h.value) || !candidateCurve && horizon?.target_period.slice(0, 7) !== h.target_period)
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
  const window = championWindow(snapshot);
  if (!window) return null;
  const year = window.year;
  const products = [...new Map(snapshot.products.map(p => [p.id, p])).values()];
  if (!products.length) return null;
  const calculations = chooseCalculations(snapshot, emptyReportChoice);
  const eligible: ChampionSelection["eligible"] = { STATISTICAL: [], ML: [], ENSEMBLE: [], COMMERCIAL_PLAN: [], ...(snapshot.planningRun ? { COLD_START: [] } : {}) };
  const runs = new Set<string>();
  const rows = new Map<string, typeof snapshot.horizons>();
  for (const h of snapshot.horizons) rows.set(h.calculation_id, [...(rows.get(h.calculation_id) ?? []), h]);
  for (const product of products) {
    const cold = snapshot.planningRun ? snapshot.provisionals.find(p => p.product_id === product.id && p.chain_id === product.chain_id
      && p.job_id === snapshot.planningRun!.run_id) : null;
    if (cold && coldAdmission(cold) && matchesOfficialSource(cold.source_context, source) && cold.horizons.length === 12
      && new Set(cold.horizons.map(h => h.horizon)).size === 12 && cold.horizons.every(h => Number.isInteger(h.horizon) && h.horizon >= 1 && h.horizon <= 12
        && h.target_period.slice(0,7) === addReportMonth(window.start, h.horizon-1) && Number.isFinite(Number(h.value)) && h.value !== null && Number(h.value) >= 0)) {
      eligible.COLD_START!.push(product.id); runs.add(snapshot.planningRun!.run_id);
    }
    const calculation = calculations.get(`${product.chain_id}/${product.id}`);
    const run = calculation?.input_snapshot.planning_run_id;
    const allowed = calculation?.input_snapshot.champion_eligible;
    if (!calculation || typeof run !== "string" || !/^[0-9a-f-]{36}$/i.test(run) || !Array.isArray(allowed)
      || !matchesOfficialSource(calculation.input_snapshot.source_context, source)) continue;
    const horizons = rows.get(calculation.id) ?? [];
    let valid = false;
    for (const family of championFamilies) {
      if (family === "COLD_START") continue;
      if (!allowed.includes(family)) continue;
      const candidate = calculation.candidate_metrics[family];
      const curve = family === "COMMERCIAL_PLAN" ? candidate?.horizons : horizons.map(h => ({ horizon: h.horizon, target_period: h.target_period,
        value: h[family === "STATISTICAL" ? "statistical_value" : family === "ML" ? "ml_value" : "ensemble_value"] }));
      if (!Array.isArray(curve) || curve.length !== 12 || new Set(curve.map(h => h.horizon)).size !== 12
        || curve.some(h => !Number.isSafeInteger(h.horizon) || h.horizon < 1 || h.horizon > 12 || typeof h.target_period !== "string"
          || h.target_period.slice(0, 7) !== addReportMonth(window.start, h.horizon - 1)
          || typeof h.value !== "number" || !Number.isFinite(h.value) || h.value < 0)) continue;
      eligible[family].push(product.id); valid = true;
    }
    if (valid) runs.add(run);
  }
  if (runs.size !== 1) return null;
  if (snapshot.planningRun && [...runs][0] !== snapshot.planningRun.run_id) return null;
  return { source_context: source, planning_year: year, run_id: [...runs][0],
    ...(snapshot.planningRun ? { window_start: window.start, window_end: window.end } : {}),
    chain_ids: [...new Set(products.map(p => p.chain_id))].sort(), product_ids: products.map(p => p.id).sort(),
    productChains: Object.fromEntries(products.map(p => [p.id, p.chain_id])),
    productNames: Object.fromEntries(products.map(p => [p.id, p.description])), eligible };
}
