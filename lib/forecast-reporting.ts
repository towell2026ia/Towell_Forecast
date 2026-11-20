import type { CalculationDetail, CalculationHorizon, LocalChampionRecord, LocalChampions, SelectionEvent, VintageCapabilities } from "./forecast-preview";
import type { PlanningRun, PlanningReport } from "./rolling-planning";
import type { Category, Chain, HistoricalRow, OperationalInventory, OperationalReadContext, Product } from "./supabase/types";
import { forecastHistoryAvailable, forecastObjective, forecastTrainingCutoff, forecastSourceAvailable, forecastSourceIdentityFields, salesSourceIdentityFields, forecastSourceState } from "./official-sales-source";

export type ReportSeries = "STATISTICAL" | "ML" | "ENSEMBLE" | "COLD_START" | "TOWELL";
export const reportSeries: Record<ReportSeries, string> = { STATISTICAL: "Estadístico", ML: "ML", ENSEMBLE: "Ensemble", COLD_START: "Cold Start provisional", TOWELL: "Forecast Towell adoptado" };
export type ReportCalculation = Omit<CalculationDetail, "horizons" | "selection_events" | "live_evaluations"> & {
  objective: string; created_by: string; evidence_mode: string; cutoff_at?: string;
  current_local_champion?: LocalChampionRecord;
};
export type ReportHorizon = CalculationHorizon & { id: string; calculation_id: string; chain_id: string; product_id: string };
export type ReportDecision = SelectionEvent & { chain_id: string; product_id: string; selected_by: string; system_suggestion: string | null };
export type ReportLive = { id: string; calculation_id: string; chain_id: string; product_id: string; horizon: number; target_period: string; candidate: string; forecast_value: number; actual_sale: number; sale_observation_id: string; sale_version_no: number; absolute_error: number; signed_error: number; evaluated_at: string };
export type ProvisionalForecast = { job_id: string; chain_id: string; product_id: string; issue_period: string; created_at: string; engine_version: string; model: string; confidence: string; source_context?: Pick<OperationalReadContext, "source_hash" | "generation">; horizons: { horizon: number; target_period: string; value: number }[] };
export type CustomerForecast = { id: string; chain_id: string; product_id: string; forecast_version_id: string; issue_period: string; version_no: number; target_period: string; value: number };
export type ReportSnapshot = { products: Product[]; chains: Chain[]; categories: Category[]; calculations: ReportCalculation[]; horizons: ReportHorizon[]; decisions: ReportDecision[]; live: ReportLive[]; provisionals: ProvisionalForecast[]; customers: CustomerForecast[]; capabilities: VintageCapabilities; sourceContext?: OperationalReadContext; localChampions?: LocalChampions; planningRun?: PlanningRun; planningEvaluation?: PlanningReport["evaluation"] };
export type ReportChoice = { version: string | null; issue: string | null; decision: "ALL" | "PENDING" | "ADOPTED"; calculationId?: string | null };
export const emptyReportChoice: ReportChoice = { version: null, issue: null, decision: "ALL" };
export function matchesOfficialSource(value: unknown, context: Pick<OperationalReadContext, "source_hash" | "generation"> & Partial<OperationalReadContext>) {
  if (!value || typeof value !== "object") return false;
  const source = value as Partial<OperationalReadContext>;
  const target = context as Partial<OperationalReadContext>;
  if (forecastSourceState(source) === "INVALID" || forecastSourceState(target) === "INVALID") return false;
  if (source.forecast_contract || target.forecast_contract) {
    const fields = forecastSourceIdentityFields(target);
    return Boolean(source.forecast_contract) && target.forecast_contract === source.forecast_contract && source.forecast_available === target.forecast_available
      && fields.every(field => source[field] === target[field]);
  }
  if (source.sales_contract === "towell-sales-v1" || target.sales_contract === "towell-sales-v1")
    return source.sales_contract === "towell-sales-v1" && target.sales_contract === "towell-sales-v1"
      && source.sales_available === target.sales_available
      && salesSourceIdentityFields.every(field => source[field] === target[field]);
  return Number.isSafeInteger(source.generation) && Number.isSafeInteger(target.generation) &&
    source.source_hash === context.source_hash && source.generation === context.generation &&
    (source.sales_available === undefined || !("sales_available" in context) || source.sales_available === (context as OperationalReadContext).sales_available);
}
type RunWindow = Pick<PlanningRun, "issue_period" | "window_start" | "window_end">;
function compatibleForecastCut(issue: string, context?: OperationalReadContext, cutoffAt?: string, run?: RunWindow) {
  if (run) return issue.slice(0, 7) === run.issue_period && (!cutoffAt || cutoffAt.slice(0, 7) === run.issue_period)
    && run.window_start === addReportMonth(run.issue_period, 1) && run.window_end === addReportMonth(run.issue_period, 12);
  if (!context?.forecast_contract && context?.sales_contract !== "towell-sales-v1") return true;
  const period = issue.slice(0, 7);
  if (context.planning_basis && period !== context.training_cutoff) return false;
  return /^20\d{2}-(0[1-9]|1[0-2])$/.test(period) && period >= context.first_period && period <= (forecastTrainingCutoff(context) ?? "")
    && (!cutoffAt || cutoffAt.slice(0, 7) === period);
}
export type ForecastMatrixRow = { product: Product; chain: string; category: string; calculation: ReportCalculation | null; provisional: ProvisionalForecast | null; adopted: ReportDecision | null; values: Record<string, Record<ReportSeries, number | null>> };
export type DatabaseSection = "history" | "observations" | "catalog" | "calculations" | "models" | "decisions" | "research" | "performance";
export type DatabasePage = { headers: string[]; rows: { id: string; cells: (string | number | null)[]; detail?: Record<string, unknown> }[]; hasMore: boolean };

export const amount = (value: unknown): number | null => (typeof value === "number" || typeof value === "string" && value.trim() !== "") && Number.isFinite(Number(value)) && Number(value) >= 0 ? Number(value) : null;
export function addReportMonth(issue: string, horizon: number) {
  const [year, month] = issue.slice(0, 7).split("-").map(Number);
  return new Date(Date.UTC(year, month - 1 + horizon, 1)).toISOString().slice(0, 7);
}
/** The planning index and the statistical lead have distinct origins. */
export function forecastTargetMonth(issue: string, horizon: number, context?: Partial<OperationalReadContext> | null, run?: RunWindow) {
  if (run) {
    if (issue.slice(0, 7) !== run.issue_period || run.window_end !== addReportMonth(run.window_start, 11)
      || run.window_start !== addReportMonth(issue, 1)) throw new Error("report_horizon_lineage");
    return addReportMonth(run.window_start, horizon - 1);
  }
  if (context?.forecast_contract === "pedido-unificado-v1") {
    if (forecastSourceState(context) !== "AVAILABLE" || issue.slice(0, 7) !== context.training_cutoff)
      throw new Error("report_horizon_lineage");
    return addReportMonth(context.planning_start!, horizon - 1);
  }
  return addReportMonth(issue, horizon);
}
export function currentMexicoMonth(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Mexico_City", year: "numeric", month: "2-digit" }).formatToParts(now);
  return `${parts.find(p => p.type === "year")!.value}-${parts.find(p => p.type === "month")!.value}`;
}
export function temporalLabel(target: string, createdAt: string | null, now = new Date()) {
  const month = target.slice(0, 7), current = currentMexicoMonth(now);
  const timing = createdAt && Date.parse(createdAt) >= Date.parse(month + "-01T00:00:00Z") ? "Emisión retrospectiva" : "Emisión anterior al mes";
  return `${month < current ? "Transcurrido" : month === current ? "En curso" : "Futuro"} · ${createdAt ? timing : "Sin cálculo"}`;
}
export function leafScopeIds(chains: Chain[], selected?: Set<string>) {
  if (selected) {
    const byId = new Map(chains.map(c => [c.id, c]));
    const parents = new Set<string>();
    for (const id of selected) {
      const visited = new Set([id]);
      let parent = byId.get(id)?.parentId;
      while (parent) {
        if (visited.has(parent)) throw new Error("report_scope_cycle");
        visited.add(parent);
        if (selected.has(parent)) parents.add(parent);
        parent = byId.get(parent)?.parentId;
      }
    }
    return new Set([...selected].filter(id => !parents.has(id)));
  }
  const parents = new Set(chains.map(c => c.parentId).filter((id): id is string => id !== null));
  return new Set(chains.filter(c => !parents.has(c.id)).map(c => c.id));
}
export function chooseCalculations(snapshot: ReportSnapshot, choice: ReportChoice) {
  const grouped = new Map<string, ReportCalculation>();
  if (!forecastSourceAvailable(snapshot.sourceContext)) return grouped;
  for (const calculation of snapshot.calculations) {
    if (snapshot.sourceContext && !matchesOfficialSource(calculation.input_snapshot?.source_context, snapshot.sourceContext)) continue;
    if (snapshot.planningRun && calculation.input_snapshot?.planning_run_id !== snapshot.planningRun.run_id) continue;
    if (!compatibleForecastCut(calculation.issue_period, snapshot.sourceContext, calculation.cutoff_at, snapshot.planningRun)) continue;
    if (calculation.objective !== forecastObjective(snapshot.sourceContext) || !["READY_FOR_DECISION", "DECIDED", "SUPERSEDED"].includes(calculation.status) ||
      choice.calculationId && calculation.id !== choice.calculationId || choice.version && calculation.calculation_code !== choice.version || choice.issue && calculation.issue_period.slice(0, 7) !== choice.issue) continue;
    const key = `${calculation.chain_id}/${calculation.product_id}`;
    const prior = grouped.get(key);
    if (!prior || calculation.calculation_no > prior.calculation_no || calculation.calculation_no === prior.calculation_no && calculation.created_at > prior.created_at) grouped.set(key, calculation);
  }
  return grouped;
}
export function validDecision(snapshot: ReportSnapshot, calculation: ReportCalculation) {
  const events = snapshot.decisions.filter(e => e.calculation_id === calculation.id && e.chain_id === calculation.chain_id && e.product_id === calculation.product_id)
    .sort((a, b) => a.selected_at.localeCompare(b.selected_at) || a.id.localeCompare(b.id));
  const event = events.at(-1);
  if (!event) return null;
  const rows = snapshot.horizons.filter(h => h.calculation_id === calculation.id);
  const field = { STATISTICAL: "statistical_value", ML: "ml_value", ENSEMBLE: "ensemble_value" }[event.selected_candidate] as "statistical_value" | "ml_value" | "ensemble_value";
  if (!field || event.selected_curve.length !== 12 || new Set(event.selected_curve.map(h => h.horizon)).size !== 12 ||
    event.selected_curve.some(h => h.target_period.slice(0, 7) !== forecastTargetMonth(calculation.issue_period, h.horizon, snapshot.sourceContext, snapshot.planningRun) ||
      amount(h.value) === null || !rows.some(r => r.horizon === h.horizon && r.target_period.slice(0, 7) === h.target_period.slice(0, 7) && amount(r[field]) === Number(h.value)))) throw new Error("decision_lineage_invalid");
  return event;
}
export function buildForecastMatrix(snapshot: ReportSnapshot, choice: ReportChoice, periodRange: [string | null, string | null]) {
  const provisionals = !forecastSourceAvailable(snapshot.sourceContext) ? [] : snapshot.sourceContext ? snapshot.provisionals.filter(row => matchesOfficialSource(row.source_context, snapshot.sourceContext!) && compatibleForecastCut(row.issue_period, snapshot.sourceContext, undefined, snapshot.planningRun)
    && (!snapshot.planningRun || row.job_id === snapshot.planningRun.run_id)) : snapshot.provisionals;
  let selected = chooseCalculations(snapshot, choice);
  const anchor = [...selected.values()].sort((a, b) => a.issue_period.localeCompare(b.issue_period) || a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id)).at(-1);
  const issue = forecastSourceAvailable(snapshot.sourceContext) ? snapshot.planningRun?.issue_period ?? anchor?.issue_period.slice(0, 7) ?? choice.issue ?? provisionals.map(p => p.issue_period).sort().at(-1) ?? null : null;
  // Product datasets and winners stay separate. Only compatible frozen curves
  // with the same objective, cut, engine and evidence mode may be consolidated.
  if (anchor) selected = chooseCalculations({ ...snapshot, calculations: snapshot.calculations.filter(c => c.objective === anchor.objective && c.issue_period.slice(0, 7) === issue && c.engine_version === anchor.engine_version && c.evidence_mode === anchor.evidence_mode) }, choice);
  const months = issue ? Array.from({ length: 12 }, (_, i) => forecastTargetMonth(issue, i + 1, snapshot.sourceContext, snapshot.planningRun)) : [];
  const periods = months.filter(p => (!periodRange[0] || p >= periodRange[0]) && (!periodRange[1] || p <= periodRange[1]));
  const leaf = leafScopeIds(snapshot.chains, new Set(snapshot.products.map(p => p.chain_id)));
  const chains = new Map(snapshot.chains.map(c => [c.id, c.name])), categories = new Map(snapshot.categories.map(c => [c.id, c.name]));
  const rows: ForecastMatrixRow[] = [];
  const uniqueProducts = [...new Map(snapshot.products.map(p => [p.id, p])).values()];
  for (const product of uniqueProducts.filter(p => leaf.has(p.chain_id))) {
    const found = selected.get(`${product.chain_id}/${product.id}`);
    const calculation = found?.issue_period.slice(0, 7) === issue ? found : null;
    const adopted = calculation ? validDecision(snapshot, calculation) : null;
    if (choice.decision === "ADOPTED" && !adopted || choice.decision === "PENDING" && adopted) continue;
    const provisional = provisionals.filter(p => p.chain_id === product.chain_id && p.product_id === product.id && p.issue_period === issue)
      .sort((a, b) => a.created_at.localeCompare(b.created_at)).at(-1) ?? null;
    const values: ForecastMatrixRow["values"] = {};
    for (const period of periods) {
      const h = calculation ? snapshot.horizons.find(r => r.calculation_id === calculation.id && r.chain_id === product.chain_id && r.product_id === product.id && r.target_period.slice(0, 7) === period) : null;
      values[period] = { STATISTICAL: amount(h?.statistical_value), ML: amount(h?.ml_value), ENSEMBLE: amount(h?.ensemble_value),
        COLD_START: amount(provisional?.horizons.find(r => r.target_period.slice(0, 7) === period)?.value), TOWELL: amount(adopted?.selected_curve.find(r => r.target_period.slice(0, 7) === period)?.value) };
    }
    if (calculation && !snapshot.planningRun && snapshot.sourceContext?.forecast_contract === "pedido-unificado-v1") {
      const bridge = calculation.input_snapshot.bridge_horizons;
      const [year, month] = calculation.issue_period.slice(0, 7).split("-").map(Number);
      const [startYear, startMonth] = snapshot.sourceContext.planning_start!.split("-").map(Number);
      const bridgeLength = (startYear - year) * 12 + startMonth - month - 1;
      if (!Array.isArray(bridge) || bridge.length !== bridgeLength) throw new Error("report_horizon_lineage");
      for (const [i, h] of bridge.entries()) {
        const period = addReportMonth(calculation.issue_period, i + 1);
        if (h?.target_period !== period || amount(h.statistical_value) === null || amount(h.ensemble_value) === null
          || h.ml_value != null && amount(h.ml_value) === null) throw new Error("report_horizon_lineage");
        values[period] = { STATISTICAL: amount(h.statistical_value), ML: amount(h.ml_value),
          ENSEMBLE: amount(h.ensemble_value), COLD_START: null, TOWELL: null };
      }
    }
    rows.push({ product, chain: chains.get(product.chain_id) ?? product.chain_id, category: categories.get(product.category_id ?? "") ?? "Sin categoría", calculation, provisional, adopted, values });
  }
  return { issue, periods, rows, excludedParentScopes: new Set(snapshot.products.filter(p => !leaf.has(p.chain_id)).map(p => p.chain_id)).size };
}
export function matrixTotal(rows: ForecastMatrixRow[], period: string, series: ReportSeries) {
  const values = rows.map(row => row.values[period]?.[series] ?? null).filter((v): v is number => v !== null);
  // Sum the stored decimal quantities before converting once for chart/XLSX.
  // Binary addition creates tails such as 1.01 + 1.02 = 2.0300000000000002.
  // Preserve each quantity's precision; this does not round historical data.
  const parts = values.map(value => {
    const [coefficient, exponent = "0"] = String(value).split("e");
    const [integer, fraction = ""] = coefficient.split(".");
    return { integer: BigInt(integer + fraction), scale: fraction.length - Number(exponent) };
  });
  const scale = Math.max(0, ...parts.map(part => part.scale));
  const total = parts.reduce((sum, part) => sum + part.integer * BigInt(10) ** BigInt(scale - part.scale), BigInt(0));
  const digits = (total < BigInt(0) ? -total : total).toString().padStart(scale + 1, "0");
  const decimal = `${total < BigInt(0) ? "-" : ""}${scale ? `${digits.slice(0, -scale)}.${digits.slice(-scale)}` : digits}`;
  return { value: values.length ? Number(decimal) : null, covered: values.length, eligible: rows.length };
}
export function comparableForecastRows(rows: ForecastMatrixRow[], periods: string[]) {
  // Fix the same exact variant set for every selected month and all families.
  // Missing ML is not zero and is never filled with another product's curve.
  return periods.length ? rows.filter(row => row.calculation && periods.every(period =>
    (["STATISTICAL", "ML", "ENSEMBLE"] as const).every(family => amount(row.values[period]?.[family]) !== null))) : [];
}
export function confirmedMissingTowellHistory(row: ForecastMatrixRow, source: OperationalReadContext | undefined,
  history: HistoricalRow[], inventory: OperationalInventory[], historyComplete = false) {
  const metric = source?.forecast_contract ? "ORDER" : "SALES";
  return historyComplete && Boolean(source?.forecast_contract || source?.sales_contract === "towell-sales-v1") && forecastHistoryAvailable(source)
    && inventory.some(item => item.chain_id === row.product.chain_id && item.product_id === row.product.id)
    && !history.some(item => item.chain_id === row.product.chain_id && item.product_id === row.product.id && item[metric] !== null);
}
export type CommercialRow = HistoricalRow & { calculation: ReportCalculation | null; customer: number | null; towellHistory: number | null; statistical: number | null; ml: number | null; ensemble: number | null; adopted: number | null; absoluteError: number | null; liveError: number | null; liveActual: number | null; wapeLive: number | null; fillRate: number | null; evidence: string };
export function commercialRows(history: HistoricalRow[], snapshot: ReportSnapshot, choice: ReportChoice, metric: Exclude<ReportSeries, "COLD_START"> = "TOWELL", now = new Date()): CommercialRow[] {
  return history.map(row => {
    const compatible = snapshot.calculations.filter(c => snapshot.horizons.some(h => h.calculation_id === c.id && h.target_period.slice(0, 7) === row.period.slice(0, 7)));
    const calculation = chooseCalculations({ ...snapshot, calculations: compatible }, choice).get(`${row.chain_id}/${row.product_id}`) ?? null;
    const h = calculation ? snapshot.horizons.find(r => r.calculation_id === calculation.id && r.chain_id === row.chain_id && r.product_id === row.product_id && r.target_period.slice(0, 7) === row.period.slice(0, 7)) : null;
    const event = calculation ? validDecision(snapshot, calculation) : null;
    const customer = snapshot.customers.filter(c => c.chain_id === row.chain_id && c.product_id === row.product_id && c.target_period.slice(0, 7) === row.period.slice(0, 7) && c.issue_period.slice(0, 7) <= row.period.slice(0, 7))
      .sort((a, b) => a.issue_period.localeCompare(b.issue_period) || a.version_no - b.version_no).at(-1);
    const adopted = h ? amount(event?.selected_curve.find(r => r.horizon === h.horizon && r.target_period.slice(0, 7) === row.period.slice(0, 7))?.value) : null;
    const values = { STATISTICAL: amount(h?.statistical_value), ML: amount(h?.ml_value), ENSEMBLE: amount(h?.ensemble_value), TOWELL: adopted };
    const forecast = values[metric], sale = row.observations?.SALES;
    const live = h && calculation?.objective === "Venta" ? snapshot.live.find(v => v.calculation_id === calculation.id && v.chain_id === row.chain_id && v.product_id === row.product_id && v.horizon === h.horizon && v.target_period === h.target_period && v.candidate === (metric === "TOWELL" ? "TOWELL_SELECTED" : metric) &&
      sale && v.sale_observation_id === sale.id && v.sale_version_no === sale.version && Number(v.actual_sale) === row.SALES && Number(v.forecast_value) === forecast && sale.availableAt &&
      Date.parse(calculation.created_at) < Date.parse(row.period.slice(0, 7) + "-01T00:00:00Z") && Date.parse(calculation.created_at) < Date.parse(sale.availableAt) &&
      Date.parse(sale.availableAt) <= now.getTime() && Date.parse(addReportMonth(row.period, 1) + "-01T00:00:00Z") <= now.getTime()) : null;
    const observed = forecastObjective(snapshot.sourceContext) === "Pedido" ? row.ORDER : row.SALES;
    const absoluteError = forecast !== null && observed !== null ? Math.abs(observed - forecast) : null;
    return { ...row, calculation: h ? calculation : null, customer: snapshot.sourceContext ? amount(row.exact?.FCST_CLIENTE_HISTORY) : amount(customer?.value), towellHistory: amount(row.exact?.FCST_TOWELL_HISTORY), statistical: values.STATISTICAL, ml: values.ML, ensemble: values.ENSEMBLE,
      adopted, absoluteError, liveError: live ? Math.abs(Number(live.actual_sale) - Number(live.forecast_value)) : null, liveActual: live ? Number(live.actual_sale) : null,
      wapeLive: live && Number(live.actual_sale) > 0 ? Math.abs(Number(live.actual_sale) - Number(live.forecast_value)) / Number(live.actual_sale) * 100 : null,
      fillRate: row.ORDER !== null && row.DELIVERY !== null && row.ORDER > 0 ? row.DELIVERY / row.ORDER * 100 : null,
      evidence: live ? "LIVE temporal verificado" : h && calculation ? temporalLabel(row.period, calculation.created_at, now) + " · Sin evidencia LIVE" : observed === null ? "Sin dato real" : "Histórico original · Sin forecast vinculado" };
  });
}
export function commercialTotals(rows: CommercialRow[]) {
  const live = rows.filter(r => r.liveError !== null && r.liveActual !== null);
  const actual = live.reduce((sum, r) => sum + r.liveActual!, 0), error = live.reduce((sum, r) => sum + r.liveError!, 0);
  const service = rows.filter(r => r.ORDER !== null && r.DELIVERY !== null);
  const orders = service.reduce((sum, r) => sum + r.ORDER!, 0), delivered = service.reduce((sum, r) => sum + r.DELIVERY!, 0);
  return { observations: rows.length, liveObservations: live.length, wapeLive: actual > 0 ? error / actual * 100 : null, fillRate: orders > 0 ? delivered / orders * 100 : null };
}
