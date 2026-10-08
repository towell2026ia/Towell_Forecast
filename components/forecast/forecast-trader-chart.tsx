"use client";
import { Area, CartesianGrid, ComposedChart, Line, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { periodLabel, quantity, series, type SeriesVisibility, type TraderPoint } from "@/lib/forecast-chart-data";

export function TraderTooltip({ active, payload }: { active?: boolean; payload?: { payload: TraderPoint }[] }) {
  if (!active || !payload?.[0]) return null;
  const point = payload[0].payload;
  return <div className="min-w-56 rounded-xl border border-slate-200 bg-white p-3 shadow-xl"><p className="font-semibold">Periodo: {periodLabel(point.period)}</p><div className="mt-3 space-y-1.5">{[...series.map(s => ({ label: s.label, value: point[s.key], color: s.color })), ...(["p10", "p50", "p90", "p95"] as const).map(key => ({ label: key.toUpperCase(), value: point[key], color: "#64748b" }))].filter(s => s.value !== null).map(s => <div key={s.label} className="flex items-center justify-between gap-6 text-xs"><span className="flex items-center gap-2 text-slate-600"><span className="size-2 rounded-full" style={{ background: s.color }}/>{s.label}</span><strong>{quantity(s.value)} pzas</strong></div>)}</div></div>;
}
// Reuses the executive dashboard's timeline, palette, null-gap lines and past/future layout;
// the adapter supplies only RLS-visible history and literal E2 results, never demo imports.
export function ForecastTraderChart({ points, cutoff, visible, onChange, previous = false }: { points: TraderPoint[]; cutoff?: string; visible: SeriesVisibility; onChange: (key: keyof SeriesVisibility) => void; previous?: boolean }) {
  const future = points.filter(p => cutoff && p.period > cutoff);
  const hasProvisional = points.some(point => point.provisional !== null);
  return <Card className="border-slate-200 shadow-sm"><CardHeader className="flex flex-wrap items-start justify-between gap-3"><div><CardTitle className="text-base">Evolución y pronóstico</CardTitle><p className="mt-1 text-sm text-slate-500">Histórico autorizado · {hasProvisional ? "estimación provisional de arranque; no es Fcst Towell oficial" : "vista previa operativa; aún no es Fcst Towell oficial"}</p></div>{previous && <span className="rounded-full bg-amber-50 px-3 py-1 text-xs text-amber-800">Vista previa anterior</span>}</CardHeader><CardContent className="p-3 md:p-6">
    <div className="mb-5 flex flex-wrap gap-2" aria-label="Series visibles">{series.map(s => { const available = points.some(p => p[s.key] !== null); return <button key={s.key} type="button" aria-pressed={visible[s.key]} disabled={!available} onClick={() => onChange(s.key)} className={`rounded-full border px-3 py-1.5 text-xs font-medium transition ${visible[s.key] && available ? "border-blue-200 bg-blue-50 text-blue-900" : "border-slate-200 text-slate-500"} disabled:opacity-50`}><span className="mr-2 inline-block size-2 rounded-full" style={{ background: s.color }}/>{s.label}{!available && " — Sin datos"}</button>; })}</div>
    <div className="mb-2 grid grid-cols-[1fr_auto_1fr] items-center text-[10px] font-bold uppercase tracking-wider text-slate-400"><span>Real / histórico</span><span className="px-2 text-blue-700">Corte {periodLabel(cutoff)}</span><span className="text-right">Forecast</span></div>
    <div className="h-[300px] w-full md:h-[360px] xl:h-[440px]" role="img" aria-label={`Evolución temporal; corte ${cutoff ?? "sin preview"}`}>
      <ResponsiveContainer width="100%" height="100%" minWidth={0}><ComposedChart data={points} margin={{ top: 12, right: 18, left: 4, bottom: 10 }}>
        <CartesianGrid vertical={false} stroke="#e2e8f0"/><XAxis dataKey="period" tickFormatter={periodLabel} tick={{ fontSize: 11, fill: "#64748b" }} minTickGap={30}/><YAxis tickFormatter={quantity} width={72} tick={{ fontSize: 11, fill: "#64748b" }}/>
        {future.length > 0 && <ReferenceArea x1={future[0].period} x2={future.at(-1)!.period} fill="#eff6ff" fillOpacity={0.75}/>}
        {cutoff && <ReferenceLine x={cutoff} stroke="#2563eb" strokeWidth={1.5} label={{ value: periodLabel(cutoff), position: "insideTopRight", fill: "#1d4ed8", fontSize: 11 }}/>}
        {visible.towell && points.some(p => p.band95) && <Area type="linear" dataKey="band95" name="P10–P95" stroke="none" fill="#93c5fd" fillOpacity={0.15} connectNulls={false} isAnimationActive={false}/>}
        {visible.towell && points.some(p => p.band90) && <Area type="linear" dataKey="band90" name="P10–P90" stroke="none" fill="#60a5fa" fillOpacity={0.22} connectNulls={false} isAnimationActive={false}/>}
        <Tooltip content={<TraderTooltip/>}/>
        {series.map(s => visible[s.key] && points.some(p => p[s.key] !== null) && <Line key={s.key} type="linear" dataKey={s.key} name={s.label} stroke={s.color} strokeWidth={s.key === "towell" ? 3.2 : 2} strokeDasharray={"dash" in s ? s.dash : undefined} dot={false} activeDot={{ r: 4 }} connectNulls={false} isAnimationActive={false}/>)}
      </ComposedChart></ResponsiveContainer>
    </div><p className="mt-3 text-xs text-slate-500">Vacíos ≠ cero. {hasProvisional ? "La línea naranja es una estimación provisional basada en comparables; no es Champion, forecast oficial ni WAPE del producto nuevo. " : ""}Histórico según filtros. Las bandas sólo aparecen cuando E2 entrega evidencia completa. Los periodos visuales no modifican el corte.</p>
  </CardContent></Card>;
}
