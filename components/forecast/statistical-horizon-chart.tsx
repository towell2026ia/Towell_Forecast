"use client";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { PreviewCandidate } from "@/lib/forecast-preview";
import { horizonAccuracy, metricEvidence } from "@/lib/statistical-preview-data";
import { percent } from "@/lib/forecast-chart-data";
import { ForecastGrid } from "./forecast-horizon-table";
export function StatisticalHorizonAccuracy({ candidate }: { candidate?: PreviewCandidate }) {
  const rows = horizonAccuracy(candidate);
  const bars = rows.filter(r => r.wape !== null).map(r => ({ ...r, label: `H${r.horizon}` }));
  return <section className="rounded-xl border bg-white p-4"><h3 className="font-semibold">WAPE por horizonte</h3><p className="mt-1 text-xs text-slate-500">Validación retrospectiva · sólo evidencia entregada por E2</p>{bars.length > 0 && <div className="h-[300px] md:h-[360px]" role="img" aria-label="WAPE retrospectivo por horizonte"><ResponsiveContainer width="100%" height="100%" minWidth={0}><BarChart data={bars}><CartesianGrid vertical={false} stroke="#e2e8f0"/><XAxis dataKey="label"/><YAxis tickFormatter={percent}/><Tooltip formatter={v => percent(typeof v === "number" ? v : null)}/><Bar dataKey="wape" name="WAPE" fill="#175cd3" isAnimationActive={false}/></BarChart></ResponsiveContainer></div>}{rows.length > 0 ? <div className="mt-3" title={metricEvidence}><ForecastGrid headers={["Horizonte", "WAPE validación"]} rows={rows.map(r => [`H${r.horizon}`, percent(r.wape)])}/></div> : <p className="mt-3 text-sm text-slate-500">Sin evidencia de WAPE por horizonte. No se reconstruyen backtests.</p>}</section>;
}
