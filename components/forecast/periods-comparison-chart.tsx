"use client";

import { useState } from "react";
import { Bar, CartesianGrid, ComposedChart, Line, ReferenceArea, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { dashboardSeries, type DashboardPoint, type DashboardSeries } from "@/lib/periods-dashboard";
import { reportNumber } from "@/app/report-controls";

const initial = new Set<DashboardSeries>(["previous2", "previous1", "actual", "STATISTICAL", "ML", "ENSEMBLE", "TOWELL"]);
export function PeriodsComparisonChart({ points, year }: { points: DashboardPoint[]; year: number }) {
  const [shown, setShown] = useState(initial);
  const label = (key: DashboardSeries) => key === "previous2" ? `Venta ${year - 2}` : key === "previous1" ? `Venta ${year - 1}` : `${dashboardSeries[key].label} ${year}`;
  const records = points.map(p => ({ ...p, ...p.values }));
  return <section aria-label="Gráfica comparativa comercial" className="min-w-0 border-y border-slate-200 bg-white py-5">
    <div className="flex flex-wrap items-center justify-between gap-2 px-4"><h2 className="text-lg font-semibold text-slate-950">Comparativo comercial</h2><span className="text-xs text-slate-500">Unidades · Ene–Sep real / Oct–Dic forecast disponible</span></div>
    <div className="mt-4 h-[360px] min-w-0 sm:h-[420px]" data-testid="periods-chart">
      <ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 900, height: 420 }}>
        <ComposedChart data={records} margin={{ top: 12, right: 18, bottom: 8, left: 4 }} accessibilityLayer>
          <CartesianGrid stroke="#e8edf2" vertical={false}/><XAxis dataKey="month" tickLine={false} axisLine={false} tick={{ fontSize: 12 }}/><YAxis width={60} tickLine={false} axisLine={false} tick={{ fontSize: 11 }} tickFormatter={v => Intl.NumberFormat("es-MX", { notation: "compact" }).format(v)}/>
          <ReferenceArea x1="Oct" x2="Dic" fill="#e4f0e9" fillOpacity={0.55} label={{ value: "Forecast", position: "insideTop", fontSize: 11, fill: "#52665a" }}/>
          <Tooltip content={({ active, label: month, payload }) => {
            if (!active || !payload?.length) return null;
            const point = points.find(p => p.month === month);
            return <div className="max-w-72 rounded-md border border-slate-200 bg-white p-3 text-xs shadow-md"><strong>{month} · {year}</strong>{point && [...shown].map(key => {
              const q = point.coverage[key];
              return <p key={key} className="mt-2"><span style={{ color: dashboardSeries[key].color }}>{label(key)}</span>: {reportNumber(q.value)}<span className="block text-slate-500">Cobertura {q.covered}/{q.eligible}{q.covered && q.covered < q.eligible ? " · Parcial" : ""}</span></p>;
            })}</div>;
          }}/>
          {(["previous2", "previous1"] as const).filter(k => shown.has(k)).map(k => <Bar key={k} dataKey={k} name={label(k)} fill={dashboardSeries[k].color} maxBarSize={26} isAnimationActive={false}/>)}
          {(Object.keys(dashboardSeries) as DashboardSeries[]).filter(k => !["previous2", "previous1"].includes(k) && shown.has(k)).map(k => <Line key={k} dataKey={k} name={label(k)} stroke={dashboardSeries[k].color} strokeWidth={k === "actual" ? 3 : 2} strokeDasharray={["actual", "order", "delivery"].includes(k) ? undefined : "6 4"} dot={{ r: 3 }} connectNulls={false} isAnimationActive={false}/>)}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
    <fieldset className="flex flex-wrap gap-x-5 gap-y-3 px-4 pt-3"><legend className="sr-only">Series de la gráfica</legend>{(Object.keys(dashboardSeries) as DashboardSeries[]).map(key => <label key={key} className="flex min-h-8 items-center gap-2 text-xs text-slate-700"><input type="checkbox" aria-label={label(key)} checked={shown.has(key)} onChange={() => setShown(previous => { const next = new Set(previous); if (next.has(key)) next.delete(key); else next.add(key); return next; })}/><span className="h-2 w-4 rounded-sm" style={{ background: dashboardSeries[key].color }}/>{label(key)}</label>)}</fieldset>
  </section>;
}
