import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { quantity, type Horizon, type TraderPoint } from "@/lib/forecast-chart-data";

export function ForecastGrid({ headers, rows }: { headers: string[]; rows: (string | number)[][] }) {
  return <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white"><Table><TableHeader><TableRow className="bg-slate-50">{headers.map(h => <TableHead key={h} className="whitespace-nowrap">{h}</TableHead>)}</TableRow></TableHeader><TableBody>{rows.map((row, i) => <TableRow key={i}>{row.map((value, j) => <TableCell key={j} className="whitespace-nowrap">{value}</TableCell>)}</TableRow>)}</TableBody></Table></div>;
}
export function ForecastHorizonTable({ horizons, product }: { horizons: Horizon[]; product: boolean }) {
  const aggregateMetrics = !product && horizons.some(r => "statistical_value" in r);
  return <section><h2 className="mb-3 font-semibold">Forecast H1–H12</h2>{horizons.length ? <ForecastGrid headers={product ? ["Horizonte", "Periodo", "Estadístico", "ML", "Fcst Towell", "P10", "P50", "P90", "P95"] : aggregateMetrics ? ["Horizonte", "Periodo", "Estadístico", "ML", "Fcst Towell"] : ["Horizonte", "Periodo", "Fcst Towell"]} rows={horizons.map(r => [`H${r.horizon}`, r.target_period, ...(product && "p50" in r ? [r.statistical_value, r.ml_value, r.forecast_towell, r.p10, r.p50, r.p90, r.p95] : aggregateMetrics && "statistical_value" in r ? [r.statistical_value, r.ml_value, r.forecast_towell] : [r.forecast_towell]).map(quantity)])}/> : <p className="text-sm text-slate-500">Sin horizontes elegibles para el filtro seleccionado.</p>}</section>;
}
export function ExecutiveComparison({ points, cutoff }: { points: TraderPoint[]; cutoff?: string }) {
  const future = points.filter(p => cutoff ? p.period > cutoff : p.towell !== null);
  const past = points.filter(p => cutoff ? p.period <= cutoff : p.towell === null);
  return <section className="space-y-4"><h2 className="font-semibold">Comparativa ejecutiva</h2>
    <div><h3 className="mb-3 text-sm font-semibold">Pronóstico · Forecast futuro</h3><ForecastGrid headers={["Periodo", "Fcst Cliente", "Estadístico", "ML", "Fcst Towell"]} rows={future.map(p => [p.period, ...[p.client, p.statistical, p.ml, p.towell].map(quantity)])}/></div>
    <details className="rounded-xl border bg-white p-4"><summary className="cursor-pointer text-sm font-semibold">Histórico</summary><div className="mt-3 max-h-96 overflow-y-auto"><ForecastGrid headers={["Periodo", "Venta", "Pedido", "Entrega", "Fcst Cliente"]} rows={past.map(p => [p.period, ...[p.sale, p.order, p.delivery, p.client].map(quantity)])}/></div></details>
  </section>;
}
