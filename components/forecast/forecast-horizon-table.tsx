import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { quantity, type Horizon, type TraderPoint } from "@/lib/forecast-chart-data";

export function ForecastGrid({ headers, rows }: { headers: string[]; rows: (string | number)[][] }) {
  return <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white"><Table><TableHeader><TableRow className="bg-slate-50">{headers.map(h => <TableHead key={h} className="whitespace-nowrap">{h}</TableHead>)}</TableRow></TableHeader><TableBody>{rows.map((row, i) => <TableRow key={i}>{row.map((value, j) => <TableCell key={j} className="whitespace-nowrap">{value}</TableCell>)}</TableRow>)}</TableBody></Table></div>;
}
export function ForecastHorizonTable({ horizons, product }: { horizons: Horizon[]; product: boolean }) {
  return <section><h2 className="mb-3 font-semibold">Forecast H1–H12</h2>{horizons.length ? <ForecastGrid headers={product ? ["Horizonte", "Periodo", "Estadístico", "ML", "Fcst Towell", "P10", "P50", "P90", "P95"] : ["Horizonte", "Periodo", "Fcst Towell"]} rows={horizons.map(r => [`H${r.horizon}`, r.target_period, ...(product && "statistical_value" in r ? [r.statistical_value, r.ml_value, r.forecast_towell, r.p10, r.p50, r.p90, r.p95] : [r.forecast_towell]).map(quantity)])}/> : <p className="text-sm text-slate-500">Sin horizontes elegibles para el filtro seleccionado.</p>}</section>;
}
export function ExecutiveComparison({ points }: { points: TraderPoint[] }) {
  return <details className="rounded-xl border bg-white p-4" open><summary className="cursor-pointer font-semibold">Comparativa ejecutiva</summary><div className="mt-3 max-h-96 overflow-y-auto"><ForecastGrid headers={["Periodo", "Fcst Cliente", "Fcst Towell", "Venta", "Pedido", "Entrega"]} rows={points.map(p => [p.period, ...[p.client, p.towell, p.sale, p.order, p.delivery].map(quantity)])}/></div></details>;
}
