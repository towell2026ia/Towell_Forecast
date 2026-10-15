import { addReportMonth, buildForecastMatrix, matrixTotal, type ForecastMatrixRow, type ReportChoice, type ReportSeries, type ReportSnapshot } from "./forecast-reporting";
import type { Filters, HistoricalRow } from "./supabase/types";
import { modelKey } from "./commercial-filters";

export const monthNames = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];
export const dashboardSeries = {
  previous2: { label: "Venta año −2", color: "#a8b7ca" }, previous1: { label: "Venta año −1", color: "#5284bd" },
  actual: { label: "Venta real", color: "#132e46" }, order: { label: "Pedido", color: "#a36b10" }, delivery: { label: "Enviado", color: "#28836e" },
  STATISTICAL: { label: "Forecast Estadístico", color: "#d8891c" }, ML: { label: "Forecast ML", color: "#087fa0" }, ENSEMBLE: { label: "Forecast Ensemble", color: "#b34f78" },
  COLD_START: { label: "Cold Start provisional", color: "#867246" }, TOWELL: { label: "Forecast Towell adoptado", color: "#23834d" },
  customer: { label: "Forecast Cliente", color: "#8263a3" },
};
export type DashboardSeries = keyof typeof dashboardSeries;
type Quantity = { value: number | null; covered: number; eligible: number };
export type DashboardPoint = { month: string; period: string; horizon: number | null; values: Record<DashboardSeries, number | null>; coverage: Record<DashboardSeries, Quantity> };
const inRange = (p: string, range: Filters["periodRange"]) => (!range[0] || p >= range[0]) && (!range[1] || p <= range[1]);
function quantity(values: (number | null)[], eligible: number): Quantity {
  const found = values.filter((v): v is number => v !== null);
  return { value: found.length ? found.reduce((sum, n) => sum + n, 0) : null, covered: found.length, eligible };
}
export const shortPeriod = (period: string) => `${monthNames[Number(period.slice(5, 7)) - 1]}-${period.slice(2, 4)}`;
export function periodsDashboard(snapshot: ReportSnapshot, history: HistoricalRow[], choice: ReportChoice, range: Filters["periodRange"]) {
  const matrix = buildForecastMatrix(snapshot, choice, range);
  const allowed = new Map(matrix.rows.map(r => [r.product.id, r.product.chain_id]));
  const rows = history.filter(r => allowed.get(r.product_id) === r.chain_id);
  const seen = new Set<string>();
  for (const row of rows) {
    const key = `${row.chain_id}/${row.product_id}/${row.period.slice(0, 7)}`;
    if (seen.has(key)) throw new Error("duplicate_dashboard_month");
    seen.add(key);
  }
  const cutoff = matrix.issue ?? rows.map(r => r.period.slice(0, 7)).sort().at(-1) ?? null;
  const eligible = allowed.size;
  const points: DashboardPoint[] = cutoff ? Array.from({ length: 12 }, (_, i) => {
    const period = addReportMonth(cutoff, i - 8);
    const metrics = {} as Record<DashboardSeries, Quantity>;
    for (const [key, offset, field] of [["previous2", -24, "SALES"], ["previous1", -12, "SALES"], ["actual", 0, "SALES"], ["order", 0, "ORDER"], ["delivery", 0, "DELIVERY"]] as const) {
      const target = addReportMonth(period, offset);
      metrics[key] = !inRange(period, range) || offset === 0 && i >= 9 ? { value: null, covered: 0, eligible } : quantity(rows.filter(r => r.period.slice(0, 7) === target).map(r => r[field]), eligible);
    }
    for (const series of ["STATISTICAL", "ML", "ENSEMBLE", "COLD_START", "TOWELL"] as ReportSeries[]) {
      metrics[series] = i >= 9 && inRange(period, range) ? matrixTotal(matrix.rows, period, series) : { value: null, covered: 0, eligible };
    }
    // Latest validated version known at the selected cut; never sum versions.
    const customer = matrix.rows.map(r => snapshot.customers.filter(c => c.chain_id === r.product.chain_id && c.product_id === r.product.id && c.target_period.slice(0, 7) === period && c.issue_period.slice(0, 7) <= cutoff)
      .sort((a, b) => a.issue_period.localeCompare(b.issue_period) || a.version_no - b.version_no).at(-1)?.value ?? null);
    metrics.customer = i >= 9 && inRange(period, range) ? quantity(customer, eligible) : { value: null, covered: 0, eligible };
    return { month: shortPeriod(period), period, horizon: i >= 9 ? i - 8 : null, coverage: metrics, values: Object.fromEntries(Object.entries(metrics).map(([k, q]) => [k, q.value])) as DashboardPoint["values"] };
  }) : [];
  const historicalPeriods = new Set(points.slice(0, 9).filter(p => inRange(p.period, range)).map(p => p.period));
  const actual = rows.filter(r => historicalPeriods.has(r.period.slice(0, 7)));
  const sum = (field: "SALES" | "ORDER" | "DELIVERY") => quantity(actual.map(r => r[field]), eligible * historicalPeriods.size).value;
  const paired = actual.filter(r => r.ORDER !== null && r.DELIVERY !== null);
  const orders = paired.reduce((s, r) => s + r.ORDER!, 0), deliveries = paired.reduce((s, r) => s + r.DELIVERY!, 0);
  const completeness = (key: DashboardSeries) => points.filter(p => p.coverage[key].covered === eligible && eligible > 0).length;
  const models = [...new Set(matrix.rows.map(r => r.product.commercial?.model ? modelKey(r.product.commercial.model) : "Sin identificar"))].sort();
  const coverage = models.map(model => {
    const products = matrix.rows.filter(r => (r.product.commercial?.model ? modelKey(r.product.commercial.model) : "Sin identificar") === model);
    const calculated = products.filter(r => r.calculation).length, decided = products.filter(r => r.adopted).length;
    return { model, total: products.length, calculated, pending: products.length - calculated, decided, undecided: products.length - decided };
  });
  return { matrix, cutoff, points, coverage, actualMonths: points.slice(0, 9).filter(p => p.coverage.actual.covered > 0).length,
    priorComplete: [completeness("previous2"), completeness("previous1")],
    kpis: { sales: sum("SALES"), orders: sum("ORDER"), delivery: sum("DELIVERY"), fillRate: orders > 0 ? deliveries / orders * 100 : null, paired: paired.length },
    available: matrix.rows.filter(r => r.calculation).length, adopted: matrix.rows.filter(r => r.adopted).length };
}
export function detailMetrics(row: ForecastMatrixRow) {
  return (["STATISTICAL", "ML", "ENSEMBLE"] as const).map(family => ({ family, wape: row.calculation?.candidate_metrics[family]?.wape ?? null }));
}
