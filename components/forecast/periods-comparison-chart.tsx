"use client";

import { useState } from "react";
import { Bar, CartesianGrid, ComposedChart, Line, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { dashboardSeries, shortPeriod, type DashboardPoint, type DashboardSeries } from "@/lib/periods-dashboard";
import { addReportMonth } from "@/lib/forecast-reporting";
import { planningSourceLabels } from "@/lib/planning-source-audit";
import { reportNumber } from "@/app/report-controls";

const initial = new Set<DashboardSeries>(["previous2", "previous1", "actual", "inventory", "STATISTICAL", "ML", "ENSEMBLE", "TOWELL"]);
export function PeriodsComparisonChart({ points, cutoff, planning }: { points: DashboardPoint[]; cutoff: string | null; planning?: { unit: string; provisional: boolean; source: "Towell" | "Cliente" | "Pedidos" } }) {
  const available: DashboardSeries[] = planning ? ["previous2", "previous1", "actual", ...(planning.provisional ? ["STATISTICAL" as const] : [])] : Object.keys(dashboardSeries) as DashboardSeries[];
  const [shown, setShown] = useState(() => new Set([...initial].filter(key => available.includes(key))));
  const sourceLabel = planning ? planningSourceLabels[planning.source] : "";
  const planningLabels: Partial<Record<DashboardSeries, string>> = { previous2: `${sourceLabel} año −2`, previous1: `${sourceLabel} año −1`, actual: sourceLabel, STATISTICAL: "Estadístico provisional" };
  const label = (key: DashboardSeries) => (planning && planningLabels[key]) || dashboardSeries[key].label;
  const partialLabel = (key: DashboardSeries) => {
    if (!["STATISTICAL", "ML", "ENSEMBLE"].includes(key)) return null;
    const partial = points.filter(point => point.horizon).map(point => point.coverage[key]).filter(row => row.value !== null && row.covered < row.eligible);
    return partial.length ? `PARCIAL ${Math.min(...partial.map(row => row.covered))}/${Math.max(...partial.map(row => row.eligible))}` : null;
  };
  const records = points.map(p => ({ ...p, ...p.values }));
  return <section aria-label="Gráfica comparativa comercial" className="min-w-0 border-y border-slate-200 bg-white py-5">
    <div className="flex flex-wrap items-center justify-between gap-2 px-4"><h2 className="text-lg font-semibold text-slate-950">Comparativo comercial 9+3</h2><span className="text-xs text-slate-500">{planning?.unit ?? "PIEZAS"} · Corte {cutoff ?? "Sin dato"} · {planning ? "9 meses históricos / 3 posteriores" : "9 meses históricos / H1–H3 almacenados"}</span></div>
    <div className="mt-4 h-[360px] min-w-0 sm:h-[420px]" data-testid="periods-chart">
      <ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 900, height: 420 }}>
        <ComposedChart data={records} margin={{ top: 12, right: 18, bottom: 8, left: 4 }} accessibilityLayer>
          <CartesianGrid stroke="#e8edf2" vertical={false}/><XAxis dataKey="month" interval={0} angle={-50} height={70} textAnchor="end" tickLine={false} axisLine={false} tick={{ fontSize: 10 }} tickFormatter={(month: string) => { const p = points.find(p => p.month === month); return p?.horizon ? `${month} H${p.horizon}` : month; }}/><YAxis width={45} tickLine={false} axisLine={false} tick={{ fontSize: 11 }} tickFormatter={v => Intl.NumberFormat("es-MX", { notation: "compact" }).format(v)}/>
          {points[9] && points[11] && <ReferenceArea x1={points[9].month} x2={points[11].month} fill="#e4f0e9" fillOpacity={0.55} label={{ value: planning ? (planning.provisional ? "Provisional" : "Sin Champion") : "H1–H3", position: "insideTop", fontSize: 11, fill: "#52665a" }}/>}
          <Tooltip content={({ active, label: month, payload }) => {
            if (!active || !payload?.length) return null;
            const point = points.find(p => p.month === month);
            return <div className="max-w-72 rounded-md border border-slate-200 bg-white p-3 text-xs shadow-md"><strong>{month}{point?.horizon ? ` · H${point.horizon}` : ""}</strong>{point && [...shown].map(key => {
              const q = point.coverage[key];
              return <p key={key} className="mt-2"><span style={{ color: dashboardSeries[key].color }}>{label(key)}{key === "previous1" || key === "previous2" ? ` · ${shortPeriod(addReportMonth(point.period, key === "previous1" ? -12 : -24))}` : ""}</span>: {q.exact ?? reportNumber(q.value)}<span className="block text-slate-500">Cobertura {q.covered}/{q.eligible}{q.covered && q.covered < q.eligible ? " · TOTAL PARCIAL" : ""}</span></p>;
            })}</div>;
          }}/>
          {(["previous2", "previous1", "inventory"] as const).filter(k => shown.has(k)).map(k => <Bar key={k} dataKey={k} name={label(k)} fill={dashboardSeries[k].color} maxBarSize={26} isAnimationActive={false}/>)}
          {(Object.keys(dashboardSeries) as DashboardSeries[]).filter(k => !["previous2", "previous1", "inventory"].includes(k) && shown.has(k)).map(k => <Line key={k} dataKey={k} name={label(k)} stroke={dashboardSeries[k].color} strokeWidth={k === "actual" ? 3 : 2} strokeDasharray={["actual", "order", "delivery"].includes(k) ? undefined : "6 4"} dot={{ r: 3 }} connectNulls={false} isAnimationActive={false}/>)}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
    <fieldset className="flex flex-wrap gap-x-5 gap-y-3 px-4 pt-3"><legend className="sr-only">Series de la gráfica</legend>{available.map(key => <label key={key} className="flex min-h-8 items-center gap-2 text-xs text-slate-700"><input type="checkbox" aria-label={label(key)} checked={shown.has(key)} onChange={() => setShown(previous => { const next = new Set(previous); if (next.has(key)) next.delete(key); else next.add(key); return next; })}/><span className="h-2 w-4 rounded-sm" style={{ background: dashboardSeries[key].color }}/>{label(key)}{partialLabel(key) && <span className="text-amber-800">{partialLabel(key)}</span>}</label>)}</fieldset>
    {!planning && <p className="mt-3 px-4 text-xs text-slate-500">Inventario reportado en piezas por cadena, formato, UPC y mes.</p>}
    <details className="mt-4 px-4 text-xs text-slate-600"><summary className="cursor-pointer">Consultar las 12 posiciones y ausencias de dato</summary><div className="mt-3 overflow-x-auto"><table aria-label="Datos de la gráfica 9+3" className="w-full text-left"><thead><tr>{["Periodo", "H", ...[...shown].map(label)].map(h => <th key={h} className="whitespace-nowrap p-2">{h}</th>)}</tr></thead><tbody>{points.map(p => <tr key={p.period}><th className="whitespace-nowrap p-2">{p.period}</th><td className="p-2">{p.horizon ? `H${p.horizon}` : "Real"}</td>{[...shown].map(k => <td key={k} className="whitespace-nowrap p-2">{p.coverage[k].exact ?? reportNumber(p.values[k])}</td>)}</tr>)}</tbody></table></div></details>
  </section>;
}
