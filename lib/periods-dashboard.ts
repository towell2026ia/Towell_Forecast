import { addReportMonth, buildForecastMatrix, matrixTotal, type ForecastMatrixRow, type ReportChoice, type ReportSeries, type ReportSnapshot } from "./forecast-reporting";
import type { Filters, HistoricalRow, OperationalFulfillment, OperationalInventory } from "./supabase/types";
import { modelKey } from "./commercial-filters";
import { forecastObjective, forecastHistoryAvailable, forecastTrainingCutoff } from "./official-sales-source";

export const monthNames = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"];
export const dashboardSeries = {
  previous2: { label: "Venta año −2", color: "#a8b7ca" }, previous1: { label: "Venta año −1", color: "#5284bd" },
  actual: { label: "Venta real", color: "#132e46" }, order: { label: "Pedido", color: "#a36b10" }, delivery: { label: "Enviado", color: "#28836e" },
  inventory: { label: "Inventario reportado", color: "#8a5a44" }, towellHistory: { label: "Fcst Towell histórico", color: "#6d7598" },
  STATISTICAL: { label: "Forecast Estadístico", color: "#d8891c" }, ML: { label: "Forecast ML", color: "#087fa0" }, ENSEMBLE: { label: "Forecast Ensemble", color: "#b34f78" },
  COLD_START: { label: "Cold Start provisional", color: "#867246" }, TOWELL: { label: "Forecast Towell adoptado", color: "#23834d" },
  customer: { label: "Forecast Cliente", color: "#8263a3" },
};
export type DashboardSeries = keyof typeof dashboardSeries;
export function dashboardLabel(series: DashboardSeries, objective: string) {
  if (objective === "Pedido") {
    if (series === "previous1") return "Pedido año −1";
    if (series === "previous2") return "Pedido año −2";
    if (series === "order") return "Pedido histórico";
    if (series === "inventory") return "Inventario en tienda";
  }
  return dashboardSeries[series].label;
}
type Quantity = { value: number | null; exact?: string; covered: number; eligible: number };
export type DashboardFillRate = { value: number | null; ordered: number | null; delivered: number | null; orderedExact: string | null; deliveredExact: string | null; covered: number; eligible: number;
  reported?: boolean; partialPeriod?: boolean; source?: "ESTADISTICA_BAASE" };
export type DashboardPoint = { month: string; period: string; horizon: number | null; outlook?: boolean; partialOrder?: boolean; values: Record<DashboardSeries, number | null>; coverage: Record<DashboardSeries, Quantity>; fillRate?: DashboardFillRate };
const inRange = (p: string, range: Filters["periodRange"]) => (!range[0] || p >= range[0]) && (!range[1] || p <= range[1]);
function quantity(values: (number | null)[], eligible: number): Quantity {
  const found = values.filter((v): v is number => v !== null);
  return { value: found.length ? found.reduce((sum, n) => sum + n, 0) : null, covered: found.length, eligible };
}
export function exactQuantity(values: (string | null)[], eligible: number): Quantity {
  const parts = values.filter((value): value is string => value !== null).map(value => value.split("."));
  if (!parts.length) return { value: null, covered: 0, eligible };
  const scale = Math.max(...parts.map(([, decimals]) => decimals?.length ?? 0));
  const total = parts.reduce((sum, [integer, decimals = ""]) => sum + BigInt(integer + decimals.padEnd(scale, "0")), BigInt(0));
  const digits = total.toString().padStart(scale + 1, "0");
  const exact = scale ? `${digits.slice(0, -scale)}.${digits.slice(-scale)}` : digits;
  return { value: Number(exact), exact, covered: parts.length, eligible };
}
function pairedFillRate(records: { ordered: string | null; delivered: string | null }[], eligible: number): DashboardFillRate {
  const pairs = records.filter((row): row is { ordered: string; delivered: string } => row.ordered !== null && row.delivered !== null);
  const ordered = exactQuantity(pairs.map(pair => pair.ordered), eligible);
  const delivered = exactQuantity(pairs.map(pair => pair.delivered), eligible);
  return { value: ordered.value !== null && ordered.value > 0 ? delivered.value! / ordered.value * 100 : null,
    ordered: ordered.value, delivered: delivered.value, orderedExact: ordered.exact ?? null, deliveredExact: delivered.exact ?? null,
    covered: pairs.length, eligible };
}
function monthlyFillRate(rows: HistoricalRow[], eligible: number): DashboardFillRate {
  const literal = (row: HistoricalRow, field: "ORDER" | "DELIVERY") => {
    const value = row[field];
    if (value === null) return null;
    const exact = row.exact?.[field] ?? String(value);
    if (!Number.isFinite(value) || value < 0 || !/^\d+(?:\.\d+)?$/.test(exact) || Number(exact) !== value) throw new Error("invalid_fill_rate_value");
    return exact;
  };
  return pairedFillRate(rows.map(row => ({ ordered: literal(row, "ORDER"), delivered: literal(row, "DELIVERY") })), eligible);
}
export const shortPeriod = (period: string) => `${monthNames[Number(period.slice(5, 7)) - 1]}-${period.slice(2, 4)}`;
export function periodsDashboard(snapshot: ReportSnapshot, history: HistoricalRow[], choice: ReportChoice, range: Filters["periodRange"], inventory: OperationalInventory[] = [], fulfillment: OperationalFulfillment[] = []) {
  const matrix = buildForecastMatrix(snapshot, choice, range);
  const allowed = new Map(matrix.rows.map(r => [r.product.id, r.product.chain_id]));
  const objective = forecastObjective(snapshot.sourceContext);
  const historicalMetric = objective === "Pedido" ? "ORDER" : "SALES";
  const rows = forecastHistoryAvailable(snapshot.sourceContext) ? history.filter(r => allowed.get(r.product_id) === r.chain_id) : [];
  const seen = new Set<string>();
  for (const row of rows) {
    const key = `${row.chain_id}/${row.product_id}/${row.period.slice(0, 7)}`;
    if (seen.has(key)) throw new Error("duplicate_dashboard_month");
    seen.add(key);
  }
  const stock = inventory.filter(row => allowed.get(row.product_id) === row.chain_id);
  const stockKeys = new Set<string>();
  for (const row of stock) {
    const key = `${row.chain_id}/${row.product_id}/${row.period.slice(0, 7)}`;
    if (stockKeys.has(key) || !/^\d+(?:\.\d+)?$/.test(row.quantity_text)) throw new Error("duplicate_or_invalid_inventory");
    stockKeys.add(key);
  }
  const source = snapshot.sourceContext;
  const separateFulfillment = source?.forecast_contract === "pedido-towell-v1";
  const fulfillmentAuthorized = separateFulfillment && source.fulfillment_available === true && source.fulfillment_source === "ESTADISTICA_BAASE"
    && /^[0-9a-f]{64}$/.test(source.fulfillment_file_sha256 ?? "");
  if (source?.fulfillment_available === true && !fulfillmentAuthorized) throw new Error("fulfillment_source_invalid");
  if (fulfillment.length && !fulfillmentAuthorized) throw new Error("fulfillment_source_invalid");
  const compliance = fulfillment.filter(row => allowed.get(row.product_id) === row.chain_id);
  const fulfillmentKeys = new Set<string>();
  for (const row of compliance) {
    const period = row.period.slice(0, 7), key = `${row.chain_id}/${row.product_id}/${period}`;
    if (fulfillmentKeys.has(key) || row.source_sha256 !== source!.fulfillment_file_sha256 || row.purchase_type !== "RS"
      || !/^20\d{2}-(0[1-9]|1[0-2])$/.test(period) || period < source!.first_period || period > source!.last_period
      || typeof row.partial_period !== "boolean" || !row.source_reference
      || [row.order_text, row.delivery_text].some(value => value !== null && (typeof value !== "string" || !/^\d+(?:\.\d+)?$/.test(value) || !Number.isFinite(Number(value))))
      || row.order_text !== null && !row.order_source_reference || row.delivery_text !== null && !row.delivery_source_reference)
      throw new Error("duplicate_or_invalid_fulfillment");
    fulfillmentKeys.add(key);
  }
  const cutoff = objective === "Pedido" ? matrix.issue ?? forecastTrainingCutoff(snapshot.sourceContext) ?? null : matrix.issue ?? snapshot.sourceContext?.last_period ?? rows.filter(r => r.SALES !== null || r.ORDER !== null || r.DELIVERY !== null).map(r => r.period.slice(0, 7)).sort().at(-1) ?? null;
  const calendarOutlook = !cutoff && objective === "Pedido" && snapshot.sourceContext?.forecast_contract === "pedido-towell-v1"
    && snapshot.sourceContext.forecast_block_code === "ORDER_CLOSURE_UNVERIFIED";
  const historySlots = !cutoff && objective === "Pedido" && !calendarOutlook ? 12 : 9;
  const axisEnd = cutoff ?? snapshot.sourceContext?.last_period ?? null;
  const eligible = allowed.size;
  const points: DashboardPoint[] = axisEnd ? Array.from({ length: 12 }, (_, i) => {
    const period = addReportMonth(axisEnd, i - historySlots + 1);
    const metrics = {} as Record<DashboardSeries, Quantity>;
    const partialOrderPeriod = fulfillmentAuthorized && Boolean(forecastTrainingCutoff(source)) && period > forecastTrainingCutoff(source)! && period <= source!.last_period;
    for (const [key, offset, field] of [["previous2", -24, historicalMetric], ["previous1", -12, historicalMetric], ["actual", 0, "SALES"], ["order", 0, "ORDER"], ["delivery", 0, "DELIVERY"]] as const) {
      const target = addReportMonth(period, offset);
      const members = rows.filter(row => row.period.slice(0, 7) === target);
      metrics[key] = !inRange(period, range) || offset === 0 && i >= historySlots && !(key === "order" && partialOrderPeriod) ? { value: null, covered: 0, eligible }
        : exactQuantity(members.map(row => row.exact?.[field] ?? (row[field] === null ? null : String(row[field]))), eligible);
    }
    metrics.inventory = inRange(period, range) ? exactQuantity(stock.filter(row => row.period.slice(0, 7) === period).map(row => row.quantity_text), eligible) : { value: null, covered: 0, eligible };
    metrics.towellHistory = inRange(period, range) ? exactQuantity(rows.filter(row => row.period.slice(0, 7) === period).map(row => row.exact?.FCST_TOWELL_HISTORY ?? null), eligible) : { value: null, covered: 0, eligible };
    for (const series of ["STATISTICAL", "ML", "ENSEMBLE", "COLD_START", "TOWELL"] as ReportSeries[]) {
      metrics[series] = !calendarOutlook && i >= historySlots && inRange(period, range) ? matrixTotal(matrix.rows, period, series) : { value: null, covered: 0, eligible };
    }
    // Latest validated version known at the selected cut; never sum versions.
    const customer = matrix.rows.map(r => snapshot.customers.filter(c => c.chain_id === r.product.chain_id && c.product_id === r.product.id && c.target_period.slice(0, 7) === period && c.issue_period.slice(0, 7) <= axisEnd)
      .sort((a, b) => a.issue_period.localeCompare(b.issue_period) || a.version_no - b.version_no).at(-1)?.value ?? null);
    metrics.customer = snapshot.sourceContext
      ? inRange(period, range) ? exactQuantity(rows.filter(row => row.period.slice(0, 7) === period).map(row => row.exact?.FCST_CLIENTE_HISTORY ?? null), eligible) : { value: null, covered: 0, eligible }
      : i >= historySlots && inRange(period, range) ? quantity(customer, eligible) : { value: null, covered: 0, eligible };
    const monthlyCompliance = inRange(period, range) ? compliance.filter(row => row.period.slice(0, 7) === period) : [];
    const complianceBasis = separateFulfillment ? pairedFillRate(monthlyCompliance.map(row => ({ ordered: row.order_text, delivered: row.delivery_text })), eligible) : null;
    const fillRate: DashboardFillRate = complianceBasis ? { ...complianceBasis, ...(fulfillmentAuthorized ? { source: "ESTADISTICA_BAASE" as const } : {}), reported: complianceBasis.covered > 0,
      partialPeriod: monthlyCompliance.some(row => row.partial_period) || Boolean(monthlyCompliance.length && cutoff && period > cutoff) } : monthlyFillRate(i < historySlots && inRange(period, range) ? rows.filter(row => row.period.slice(0, 7) === period) : [], eligible);
    return { month: shortPeriod(period), period, horizon: cutoff && i >= historySlots ? i - historySlots + 1 : null,
      ...(calendarOutlook && i >= historySlots ? { outlook: true } : {}),
      ...(partialOrderPeriod && metrics.order.covered > 0 ? { partialOrder: true } : {}),
      coverage: metrics, values: Object.fromEntries(Object.entries(metrics).map(([k, q]) => [k, q.value])) as DashboardPoint["values"], fillRate };
  }) : [];
  const historicalPeriods = new Set(points.slice(0, historySlots).filter(p => inRange(p.period, range)).map(p => p.period));
  const actual = rows.filter(r => historicalPeriods.has(r.period.slice(0, 7)));
  const sum = (field: "SALES" | "ORDER" | "DELIVERY") => quantity(actual.map(r => r[field]), eligible * historicalPeriods.size).value;
  const fulfillmentPeriods = new Set((separateFulfillment ? points.filter(point => point.period <= source!.last_period) : points.slice(0, historySlots)).filter(point => inRange(point.period, range)).map(point => point.period));
  const reportedCompliance = compliance.filter(row => fulfillmentPeriods.has(row.period.slice(0, 7)));
  const fillRateBasis = separateFulfillment
    ? pairedFillRate(reportedCompliance.map(row => ({ ordered: row.order_text, delivered: row.delivery_text })), eligible * fulfillmentPeriods.size)
    : monthlyFillRate(actual, eligible * historicalPeriods.size);
  const completeness = (key: DashboardSeries) => points.filter(p => p.coverage[key].covered === eligible && eligible > 0).length;
  const models = [...new Map(matrix.rows.map(r => {
    const model = r.product.commercial?.model ? modelKey(r.product.commercial.model) : "Sin identificar", chainId = r.product.chain_id;
    const key = JSON.stringify([chainId, model]);
    return [key, { key, chainId, chain: r.chain, model }];
  })).values()].sort((a, b) => a.model.localeCompare(b.model) || a.chain.localeCompare(b.chain) || a.chainId.localeCompare(b.chainId));
  const coverage = models.map(group => {
    const products = matrix.rows.filter(r => r.product.chain_id === group.chainId && (r.product.commercial?.model ? modelKey(r.product.commercial.model) : "Sin identificar") === group.model);
    const calculated = products.filter(r => r.calculation).length, decided = products.filter(r => r.adopted).length;
    return { ...group, total: products.length, calculated, pending: products.length - calculated, decided, undecided: products.length - decided };
  });
  const visiblePeriods = new Set(points.filter(point => inRange(point.period, range)).map(point => point.period));
  return { matrix, cutoff, points, coverage, objective, historySlots, fulfillment: compliance.filter(row => visiblePeriods.has(row.period.slice(0, 7))), fulfillmentPeriods: [...fulfillmentPeriods], sourceContext: snapshot.sourceContext, actualMonths: points.slice(0, historySlots).filter(p => p.coverage[objective === "Pedido" ? "order" : "actual"].covered > 0).length,
    priorComplete: [completeness("previous2"), completeness("previous1")],
    kpis: { sales: sum("SALES"), orders: sum("ORDER"), delivery: sum("DELIVERY"), fillRate: fillRateBasis.value, paired: fillRateBasis.covered, expectedPairs: fillRateBasis.eligible,
      fillRateOrderedExact: fillRateBasis.orderedExact, fillRateDeliveredExact: fillRateBasis.deliveredExact,
      fillRateSource: fulfillmentAuthorized ? "ESTADISTICA_BAASE" : null, fillRatePartialPeriod: reportedCompliance.some(row => row.partial_period || cutoff && row.period.slice(0, 7) > cutoff) },
    available: matrix.rows.filter(r => r.calculation).length, adopted: matrix.rows.filter(r => r.adopted).length };
}
export function detailMetrics(row: ForecastMatrixRow) {
  return (["STATISTICAL", "ML", "ENSEMBLE"] as const).map(family => ({ family, wape: row.calculation?.candidate_metrics[family]?.wape ?? null }));
}
