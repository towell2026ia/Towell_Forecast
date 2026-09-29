import type { Filters } from "./supabase/types";
import type { PreviewAggregate, PreviewHorizon, PreviewScope } from "./forecast-preview";

export type HistoricalMonth = { period: string; sale: number | null; order: number | null; delivery: number | null };
export type CustomerMonth = { period: string; value: number };
export type TraderPoint = HistoricalMonth & { client: number | null; towell: number | null; statistical: number | null; ml: number | null; p10: number | null; p50: number | null; p90: number | null; p95: number | null; band90: [number, number] | null; band95: [number, number] | null };
export type Horizon = PreviewHorizon | PreviewAggregate;
export const series = [
  { key: "sale", label: "Venta", color: "#175cd3" },
  { key: "order", label: "Pedido", color: "#7c3aed" },
  { key: "delivery", label: "Entrega", color: "#0891b2" },
  { key: "client", label: "Fcst Cliente", color: "#f59e0b", dash: "5 4" },
  { key: "towell", label: "Fcst Towell", color: "#0f172a" },
  { key: "statistical", label: "Estadístico", color: "#64748b", dash: "7 5" },
  { key: "ml", label: "Machine Learning", color: "#c026d3", dash: "2 4" },
] as const;
export type SeriesKey = typeof series[number]["key"];
export type SeriesVisibility = Record<SeriesKey, boolean>;
export const defaultVisibility: SeriesVisibility = { sale: true, order: true, delivery: true, client: false, towell: true, statistical: false, ml: false };
export const quantity = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? "—" : value.toLocaleString("es-MX", { maximumFractionDigits: 2 });
export const percent = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? "—" : `${quantity(value)}%`;
export function periodLabel(period: string | null | undefined) {
  if (!period) return "—";
  const [year, month] = period.split("-").map(Number);
  return `${["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"][month - 1]}-${year}`;
}
export function addMonth(period: string, offset: number) {
  const [year, month] = period.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1 + offset, 1));
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;
}
export function scopeHorizons(scope: PreviewScope | null, filters: Filters): Horizon[] {
  if (!scope || !filters.chainId || scope.chain_id !== filters.chainId || !scope.issue_period) return [];
  // A product-scoped run cannot masquerade as the full category/chain aggregate.
  let rows: Horizon[];
  if (filters.productId) rows = scope.products?.find(p => p.product_id === filters.productId && (!filters.categoryId || p.category_id === filters.categoryId))?.horizons ?? [];
  else if (filters.search.trim()) rows = [];
  else rows = scope.aggregates?.filter(r => r.level === (filters.categoryId ? "category" : "chain") && r.key === (filters.categoryId ?? filters.chainId)) ?? [];
  // Dates and numbers remain backend values; malformed/partial results are not repaired.
  if (rows.length !== 12 || new Set(rows.map(r => r.horizon)).size !== 12 || rows.some(r => r.horizon < 1 || r.horizon > 12 || r.target_period !== addMonth(scope.issue_period!, r.horizon) || !Number.isFinite(r.forecast_towell))) return [];
  return [...rows].sort((a, b) => a.horizon - b.horizon);
}
export function traderPoints(history: HistoricalMonth[], customer: CustomerMonth[], horizons: Horizon[], cutoff: string | undefined, filters: Filters): TraderPoint[] {
  if (!filters.chainId) return [];
  const historical = history.filter(r => (!cutoff || r.period <= cutoff) && (!filters.periodRange[0] || r.period >= filters.periodRange[0]) && (!filters.periodRange[1] || r.period <= filters.periodRange[1]));
  const byPeriod = new Map(historical.map(r => [r.period, r]));
  const fcst = new Map(horizons.map(r => [r.target_period, r]));
  const client = new Map(customer.filter(r => cutoff && (r.period > cutoff || (!filters.periodRange[0] || r.period >= filters.periodRange[0]) && (!filters.periodRange[1] || r.period <= filters.periodRange[1]))).map(r => [r.period, r.value]));
  const dates = [...byPeriod.keys(), ...fcst.keys(), ...client.keys(), ...(cutoff ? [cutoff] : [])].sort();
  if (!dates.length) return [];
  const points: TraderPoint[] = [];
  for (let period = dates[0], count = 0; period <= dates.at(-1)! && count < 2400; period = addMonth(period, 1), count++) {
    const actual = byPeriod.get(period), future = fcst.get(period);
    const product = future && "statistical_value" in future ? future : null;
    const bands = product && [product.p10, product.p50, product.p90, product.p95].every(v => v !== null && Number.isFinite(v)) && product.p10! <= product.p50 && product.p50 <= product.p90! && product.p90! <= product.p95!;
    points.push({ period, sale: actual?.sale ?? null, order: actual?.order ?? null, delivery: actual?.delivery ?? null,
      client: client.get(period) ?? null, towell: future?.forecast_towell ?? null, statistical: product?.statistical_value ?? null, ml: product?.ml_value ?? null,
      p10: product?.p10 ?? null, p50: product?.p50 ?? null, p90: product?.p90 ?? null, p95: product?.p95 ?? null,
      band90: bands ? [product.p10!, product.p90!] : null, band95: bands ? [product.p10!, product.p95!] : null });
  }
  return points;
}
