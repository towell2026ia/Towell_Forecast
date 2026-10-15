import type { CalculationDetail, CalculationHorizon, SelectionEvent, VintageCapabilities } from "./forecast-preview";
import type { Category, Chain, HistoricalRow, Product } from "./supabase/types";

export type ReportSeries = "STATISTICAL" | "ML" | "ENSEMBLE" | "COLD_START" | "TOWELL";
export const reportSeries: Record<ReportSeries, string> = { STATISTICAL: "Estadístico", ML: "ML", ENSEMBLE: "Ensemble", COLD_START: "Cold Start provisional", TOWELL: "Forecast Towell adoptado" };
export type ReportCalculation = Omit<CalculationDetail, "horizons" | "selection_events" | "live_evaluations"> & {
  objective: string; created_by: string; evidence_mode: string;
};
export type ReportHorizon = CalculationHorizon & { id: string; calculation_id: string; chain_id: string; product_id: string };
export type ReportDecision = SelectionEvent & { chain_id: string; product_id: string; selected_by: string; system_suggestion: string | null };
export type ReportLive = { id: string; calculation_id: string; chain_id: string; product_id: string; horizon: number; target_period: string; candidate: string; forecast_value: number; actual_sale: number; sale_observation_id: string; sale_version_no: number; absolute_error: number; signed_error: number; evaluated_at: string };
export type ProvisionalForecast = { job_id: string; chain_id: string; product_id: string; issue_period: string; created_at: string; engine_version: string; model: string; confidence: string; horizons: { horizon: number; target_period: string; value: number }[] };
export type CustomerForecast = { id: string; chain_id: string; product_id: string; forecast_version_id: string; issue_period: string; version_no: number; target_period: string; value: number };
export type ReportSnapshot = { products: Product[]; chains: Chain[]; categories: Category[]; calculations: ReportCalculation[]; horizons: ReportHorizon[]; decisions: ReportDecision[]; live: ReportLive[]; provisionals: ProvisionalForecast[]; customers: CustomerForecast[]; capabilities: VintageCapabilities };
export type ReportChoice = { version: string | null; issue: string | null; decision: "ALL" | "PENDING" | "ADOPTED"; calculationId?: string | null };
export const emptyReportChoice: ReportChoice = { version: null, issue: null, decision: "ALL" };
export type ForecastMatrixRow = { product: Product; chain: string; category: string; calculation: ReportCalculation | null; provisional: ProvisionalForecast | null; adopted: ReportDecision | null; values: Record<string, Record<ReportSeries, number | null>> };
export type DatabaseSection = "history" | "observations" | "catalog" | "calculations" | "models" | "decisions" | "research" | "performance";
export type DatabasePage = { headers: string[]; rows: { id: string; cells: (string | number | null)[]; detail?: Record<string, unknown> }[]; hasMore: boolean };

export const amount = (value: unknown): number | null => (typeof value === "number" || typeof value === "string" && value.trim() !== "") && Number.isFinite(Number(value)) && Number(value) >= 0 ? Number(value) : null;
export function addReportMonth(issue: string, horizon: number) {
  const [year, month] = issue.slice(0, 7).split("-").map(Number);
  return new Date(Date.UTC(year, month - 1 + horizon, 1)).toISOString().slice(0, 7);
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
  for (const calculation of snapshot.calculations) {
    if (calculation.objective !== "Venta" || !["READY_FOR_DECISION", "DECIDED", "SUPERSEDED"].includes(calculation.status) ||
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
    event.selected_curve.some(h => h.target_period.slice(0, 7) !== addReportMonth(calculation.issue_period, h.horizon) ||
      amount(h.value) === null || !rows.some(r => r.horizon === h.horizon && r.target_period.slice(0, 7) === h.target_period.slice(0, 7) && amount(r[field]) === Number(h.value)))) throw new Error("decision_lineage_invalid");
  return event;
}
export function buildForecastMatrix(snapshot: ReportSnapshot, choice: ReportChoice, periodRange: [string | null, string | null]) {
  let selected = chooseCalculations(snapshot, choice);
  const anchor = [...selected.values()].sort((a, b) => a.issue_period.localeCompare(b.issue_period) || a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id)).at(-1);
  const issue = anchor?.issue_period.slice(0, 7) ?? choice.issue ?? snapshot.provisionals.map(p => p.issue_period).sort().at(-1) ?? null;
  // Product datasets and winners stay separate. Only compatible frozen curves
  // with the same objective, cut, engine and evidence mode may be consolidated.
  if (anchor) selected = chooseCalculations({ ...snapshot, calculations: snapshot.calculations.filter(c => c.objective === anchor.objective && c.issue_period.slice(0, 7) === issue && c.engine_version === anchor.engine_version && c.evidence_mode === anchor.evidence_mode) }, choice);
  const months = issue ? Array.from({ length: 12 }, (_, i) => addReportMonth(issue, i + 1)) : [];
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
    const provisional = snapshot.provisionals.filter(p => p.chain_id === product.chain_id && p.product_id === product.id && p.issue_period === issue)
      .sort((a, b) => a.created_at.localeCompare(b.created_at)).at(-1) ?? null;
    const values: ForecastMatrixRow["values"] = {};
    for (const period of periods) {
      const h = calculation ? snapshot.horizons.find(r => r.calculation_id === calculation.id && r.chain_id === product.chain_id && r.product_id === product.id && r.target_period.slice(0, 7) === period) : null;
      values[period] = { STATISTICAL: amount(h?.statistical_value), ML: amount(h?.ml_value), ENSEMBLE: amount(h?.ensemble_value),
        COLD_START: amount(provisional?.horizons.find(r => r.target_period.slice(0, 7) === period)?.value), TOWELL: amount(adopted?.selected_curve.find(r => r.target_period.slice(0, 7) === period)?.value) };
    }
    rows.push({ product, chain: chains.get(product.chain_id) ?? product.chain_id, category: categories.get(product.category_id ?? "") ?? "Sin categoría", calculation, provisional, adopted, values });
  }
  return { issue, periods, rows, excludedParentScopes: new Set(snapshot.products.filter(p => !leaf.has(p.chain_id)).map(p => p.chain_id)).size };
}
export function matrixTotal(rows: ForecastMatrixRow[], period: string, series: ReportSeries) {
  const values = rows.map(row => row.values[period]?.[series] ?? null).filter((v): v is number => v !== null);
  return { value: values.length ? values.reduce((sum, v) => sum + v, 0) : null, covered: values.length, eligible: rows.length };
}
export type CommercialRow = HistoricalRow & { calculation: ReportCalculation | null; customer: number | null; statistical: number | null; ml: number | null; ensemble: number | null; adopted: number | null; absoluteError: number | null; liveError: number | null; liveActual: number | null; wapeLive: number | null; fillRate: number | null; evidence: string };
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
    const live = h && calculation ? snapshot.live.find(v => v.calculation_id === calculation.id && v.chain_id === row.chain_id && v.product_id === row.product_id && v.horizon === h.horizon && v.target_period === h.target_period && v.candidate === (metric === "TOWELL" ? "TOWELL_SELECTED" : metric) &&
      sale && v.sale_observation_id === sale.id && v.sale_version_no === sale.version && Number(v.actual_sale) === row.SALES && Number(v.forecast_value) === forecast && sale.availableAt &&
      Date.parse(calculation.created_at) < Date.parse(row.period.slice(0, 7) + "-01T00:00:00Z") && Date.parse(calculation.created_at) < Date.parse(sale.availableAt) &&
      Date.parse(sale.availableAt) <= now.getTime() && Date.parse(addReportMonth(row.period, 1) + "-01T00:00:00Z") <= now.getTime()) : null;
    const absoluteError = forecast !== null && row.SALES !== null ? Math.abs(row.SALES - forecast) : null;
    return { ...row, calculation: h ? calculation : null, customer: amount(customer?.value), statistical: values.STATISTICAL, ml: values.ML, ensemble: values.ENSEMBLE,
      adopted, absoluteError, liveError: live ? Math.abs(Number(live.actual_sale) - Number(live.forecast_value)) : null, liveActual: live ? Number(live.actual_sale) : null,
      wapeLive: live && Number(live.actual_sale) > 0 ? Math.abs(Number(live.actual_sale) - Number(live.forecast_value)) / Number(live.actual_sale) * 100 : null,
      fillRate: row.ORDER !== null && row.DELIVERY !== null && row.ORDER > 0 ? row.DELIVERY / row.ORDER * 100 : null,
      evidence: live ? "LIVE temporal verificado" : h && calculation ? temporalLabel(row.period, calculation.created_at, now) + " · Sin evidencia LIVE" : row.SALES === null ? "Sin dato real" : "Histórico original · Sin forecast vinculado" };
  });
}
export function commercialTotals(rows: CommercialRow[]) {
  const live = rows.filter(r => r.liveError !== null && r.liveActual !== null);
  const actual = live.reduce((sum, r) => sum + r.liveActual!, 0), error = live.reduce((sum, r) => sum + r.liveError!, 0);
  const service = rows.filter(r => r.ORDER !== null && r.DELIVERY !== null);
  const orders = service.reduce((sum, r) => sum + r.ORDER!, 0), delivered = service.reduce((sum, r) => sum + r.DELIVERY!, 0);
  return { observations: rows.length, liveObservations: live.length, wapeLive: actual > 0 ? error / actual * 100 : null, fillRate: orders > 0 ? delivered / orders * 100 : null };
}
