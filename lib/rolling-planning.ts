import { addReportMonth, amount, matchesOfficialSource } from "./forecast-reporting";
import type { ProvisionalForecast, ReportCalculation, ReportHorizon, ReportSnapshot } from "./forecast-reporting";
import type { OperationalReadContext } from "./supabase/types";
import type { PreviewClient } from "./forecast-preview";
import { verifiedLocalChampions } from "./local-champion";

export interface PlanningRun {
  run_id: string;
  status: "READY" | "QUEUED" | "RUNNING" | "FAILED";
  created_at: string;
  issue_period: string;
  window_start: string;
  window_end: string;
  source_hash: string;
  generation: number;
  error_code?: string | null;
}
export interface PlanningScope {
  source_context: OperationalReadContext;
  product_ids: string[];
  chain_ids: string[];
}
export type EvaluatedFamily = "STATISTICAL" | "ML" | "ENSEMBLE";
export interface ScopeEvaluation {
  families: Partial<Record<EvaluatedFamily, { wape: number | null; bias: number | null; observations: number }>>;
  product_ids: string[];
  covered_variants: number;
  total_variants: number;
  scope_leader: { family: EvaluatedFamily } | null;
  bias_convention: string;
  observations: number;
}
export interface PlanningReport {
  run_id: string;
  window_start: string;
  window_end: string;
  source_context: OperationalReadContext;
  products: { product_id: string; chain_id: string }[];
  calculations: ReportCalculation[];
  horizons: ReportHorizon[];
  provisionals: ProvisionalForecast[];
  evaluation?: ScopeEvaluation;
}
export function checkPlanningRun(run: PlanningRun, source: OperationalReadContext) {
  if (!run || !/^[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}$/i.test(run.run_id)
    || !["READY", "QUEUED", "RUNNING", "FAILED"].includes(run.status)
    || !Number.isFinite(Date.parse(run.created_at)) || !/^20\d{2}-(?:0[1-9]|1[0-2])$/.test(run.issue_period)
    || run.source_hash !== source.source_hash || run.generation !== source.generation
    || run.window_start !== addReportMonth(run.issue_period, 1) || run.window_end !== addReportMonth(run.issue_period, 12))
    throw new Error("planning_run_lineage");
  return run;
}

/** Preserve commercial catalog/history, replace every forecast-bearing channel with this run's scoped data. */
export async function readPlanningReport(base: ReportSnapshot, runId: string, client: PreviewClient): Promise<ReportSnapshot> {
  const source = base.sourceContext;
  if (!source || !client.planningRun || !client.planningReport) throw new Error("planning_run_unavailable");
  const run = checkPlanningRun(await client.planningRun(runId), source);
  if (run.status !== "READY" || run.run_id !== runId) throw new Error("planning_run_not_ready");
  const request = { source_context: source, chain_ids: [...new Set(base.products.map(p => p.chain_id))].sort(),
    product_ids: base.products.map(p => p.id).sort() };
  const snapshot: ReportSnapshot = { ...base, calculations: [], horizons: [], provisionals: [],
    localChampions: undefined, decisions: [], live: [], planningRun: run };
  if (!request.product_ids.length) return snapshot;
  const result = await client.planningReport(runId, request);
  if (result.run_id !== runId || result.window_start !== run.window_start || result.window_end !== run.window_end
    || !matchesOfficialSource(result.source_context, source)) throw new Error("planning_report_lineage");
  const catalog = new Map(base.products.map(p => [p.id, p.chain_id]));
  const members = new Map(result.products.map(p => [p.product_id, p.chain_id]));
  if (members.size !== result.products.length || result.products.some(p => catalog.get(p.product_id) !== p.chain_id))
    throw new Error("planning_report_scope");
  const headers = new Map(result.calculations.map(row => [row.id, row.product_id]));
  const allowed = (row: { product_id: string; chain_id: string }) => members.get(row.product_id) === row.chain_id;
  const horizonKeys = new Set(result.horizons.map(h => `${h.calculation_id}/${h.horizon}`));
  if (headers.size !== result.calculations.length || horizonKeys.size !== result.horizons.length
    || new Set(result.calculations.map(c => c.product_id)).size !== headers.size
    || new Set(result.provisionals.map(c => c.product_id)).size !== result.provisionals.length
    || result.calculations.some(c => !allowed(c) || c.objective !== "Pedido" || c.input_snapshot?.planning_run_id !== runId
    || !matchesOfficialSource(c.input_snapshot?.source_context, source) || c.issue_period.slice(0,7) !== run.issue_period)
    || result.horizons.some(h => !allowed(h) || headers.get(h.calculation_id) !== h.product_id
      || !Number.isInteger(h.horizon) || h.horizon < 1 || h.horizon > 12 || h.target_period.slice(0,7) !== addReportMonth(run.issue_period,h.horizon))
    || result.provisionals.some(p => !allowed(p) || p.job_id !== runId || !matchesOfficialSource(p.source_context,source)
      || p.horizons.length !== 12 || p.horizons.some((h,i) => h.horizon !== i+1 || h.target_period.slice(0,7) !== addReportMonth(run.issue_period,i+1)
        || amount(h.value) === null))) throw new Error("planning_report_lineage");
  snapshot.calculations = result.calculations;
  snapshot.horizons = result.horizons;
  snapshot.provisionals = result.provisionals;
  snapshot.planningEvaluation = result.evaluation;
  if (members.size && client.currentChampion) {
    const productIds = [...members.keys()].sort();
    const current = await client.currentChampion({ source_context: source, run_id: runId, window_start: run.window_start, window_end: run.window_end,
      planning_year: Number(run.window_start.slice(0,4)), chain_ids: [...new Set(members.values())].sort(), product_ids: productIds });
    snapshot.localChampions = verifiedLocalChampions({ ...snapshot, products: base.products.filter(p => members.has(p.id)) }, current);
  }
  return snapshot;
}
