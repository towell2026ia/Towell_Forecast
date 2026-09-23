"use client";

import { useMemo, useState } from "react";
import { Activity, AlertTriangle, ArrowDownRight, ArrowUpRight, CheckCircle2, GitCompareArrows, LockKeyhole, Play, RefreshCw } from "lucide-react";
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import payload from "./data/forecast-demo.json";

type Target = "Venta" | "Pedido";
type Point = { period: string; actual: number | null; forecast: number | null };
type Comparison = { model: string; wape: number; bias: number; stability: number; score: number };
type Alert = { type: string; severity: string; message: string };
type DataQualityAlert = { code: string; severity: string; message: string };
type Series = {
  series_id: string; label: string; target: Target; status: string; reason?: string;
  classification?: string; winner?: string; wape?: number; bias?: number; stability?: number;
  last_closed_period?: string; history?: Point[]; forecast?: Point[]; comparisons?: Comparison[];
  alerts?: Alert[]; explanation?: string;
};

const data = payload as { run: { version: string; engine_version: string; status: string; generated_at: string; frozen: boolean; cutoff: string }; chain: string; data_quality: DataQualityAlert[]; series: Series[] };
const fmt = new Intl.NumberFormat("es-MX", { maximumFractionDigits: 0 });

function Metric({ label, value, detail, tone = "blue" }: { label: string; value: string; detail: string; tone?: "blue" | "green" | "amber" | "violet" }) {
  const colors = { blue: "bg-blue-50 text-blue-700", green: "bg-emerald-50 text-emerald-700", amber: "bg-amber-50 text-amber-700", violet: "bg-violet-50 text-violet-700" };
  return <Card className="border-slate-200 shadow-sm"><CardContent className="p-5"><div className={`mb-4 grid size-9 place-items-center rounded-xl ${colors[tone]}`}><Activity className="size-4"/></div><p className="text-sm text-slate-500">{label}</p><p className="mt-1 text-2xl font-semibold tracking-tight">{value}</p><p className="mt-2 text-xs text-slate-500">{detail}</p></CardContent></Card>;
}

export default function StatisticalEngineView({ supabaseConfigured }: { supabaseConfigured: boolean }) {
  const [target, setTarget] = useState<Target>("Venta");
  const candidates = useMemo(() => data.series.filter((row) => row.target === target), [target]);
  const [seriesId, setSeriesId] = useState("total-fendi-bd");
  const selected = candidates.find((row) => row.series_id === seriesId) ?? candidates[0];

  const chart = useMemo(() => {
    if (!selected?.history || !selected.forecast) return [];
    return [...selected.history.slice(-18), ...selected.forecast];
  }, [selected]);

  async function requestRun() {
    if (!supabaseConfigured) {
      toast.warning("Ejecución no enviada", { description: "Conecta Supabase para crear una corrida oficial. La vista actual es una ejecución reproducible de validación." });
      return;
    }
    const response = await fetch("/api/forecast-runs", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ target }) });
    if (response.ok) toast.success("Corrida solicitada", { description: "El backend la dejó en estado pendiente con trazabilidad." });
    else toast.error("No fue posible solicitar la corrida");
  }

  return <div className="space-y-6">
    <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
      <div><p className="text-xs font-bold uppercase tracking-[.16em] text-blue-700">PRD 03 · Forecast estadístico</p><h1 className="mt-2 text-3xl font-semibold tracking-[-.035em] text-slate-950">Motor Estadístico</h1><p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500">Clasifica cada serie, compara métodos con backtesting de origen rodante y congela el pronóstico ganador a 12 meses.</p></div>
      <div className="flex flex-wrap gap-2"><Badge variant="outline" className="border-emerald-200 bg-emerald-50 px-3 py-1 text-emerald-700"><LockKeyhole className="mr-1 size-3.5"/> {data.run.version} congelada</Badge><Button onClick={requestRun}><Play/> Solicitar recálculo</Button></div>
    </div>

    <div className="rounded-2xl border border-blue-200 bg-blue-50 p-4"><div className="flex gap-3"><GitCompareArrows className="mt-0.5 size-5 shrink-0 text-blue-700"/><div><p className="text-sm font-semibold text-blue-950">Baseline estadístico dinámico</p><p className="mt-1 text-sm leading-6 text-blue-800">El cálculo inicia en el primer mes con demanda para no confundir meses previos al lanzamiento con intermitencia. La gráfica incluye predicciones históricas fuera de muestra contra el real.</p></div></div></div>

    <Card className="border-slate-200 shadow-sm"><CardContent className="grid gap-4 p-5 md:grid-cols-[220px_1fr_auto]"><label className="space-y-2"><span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Objetivo</span><Select value={target} onValueChange={(value) => { const next=value as Target; setTarget(next); setSeriesId(data.series.find((row)=>row.target===next)?.series_id ?? ""); }}><SelectTrigger className="w-full"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="Venta">Venta</SelectItem><SelectItem value="Pedido">Pedido</SelectItem></SelectContent></Select></label><label className="space-y-2"><span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Serie</span><Select value={selected?.series_id ?? ""} onValueChange={setSeriesId}><SelectTrigger className="w-full"><SelectValue/></SelectTrigger><SelectContent>{candidates.map((row) => <SelectItem key={`${row.target}-${row.series_id}`} value={row.series_id}>{row.label}</SelectItem>)}</SelectContent></Select></label><div className="flex items-end"><div className="rounded-xl bg-slate-50 px-4 py-2.5 text-sm"><span className="text-slate-500">Cadena</span><span className="ml-2 font-semibold">{data.chain}</span></div></div></CardContent></Card>

    {!selected || selected.status === "insufficient" ? <Card className="border-amber-200 bg-white shadow-sm"><CardContent className="flex min-h-56 flex-col items-center justify-center p-8 text-center"><AlertTriangle className="size-10 text-amber-500"/><h2 className="mt-4 text-xl font-semibold">Datos insuficientes para {target}</h2><p className="mt-2 max-w-xl text-sm leading-6 text-slate-500">{selected?.reason ?? "No existe una serie disponible."}</p><p className="mt-4 text-xs font-medium text-slate-500">Estado: insuficiente · sin pronóstico artificial</p></CardContent></Card> : <>
      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5"><Metric label="Modelo ganador" value={selected.winner ?? "—"} detail="Score controlado" tone="blue"/><Metric label="Clasificación" value={selected.classification ?? "—"} detail="Detección automática" tone="violet"/><Metric label="WAPE" value={`${selected.wape?.toFixed(1)}%`} detail="Error primario" tone={selected.wape! < 35 ? "green" : "amber"}/><Metric label="Sesgo" value={`${selected.bias! > 0 ? "+" : ""}${selected.bias?.toFixed(1)}%`} detail={selected.bias! > 0 ? "Subpronóstico" : "Sobrepronóstico"} tone="amber"/><Metric label="Estabilidad" value={`${selected.stability?.toFixed(1)}%`} detail="Dispersión del error" tone="green"/></section>

      <Card className="border-slate-200 shadow-sm"><CardHeader className="flex flex-row items-start justify-between gap-3"><div><CardTitle className="text-base">Real vs. forecast estadístico</CardTitle><p className="mt-1 text-sm text-slate-500">Histórico punteado: predicción fuera de muestra · después del corte: horizonte futuro</p></div><Badge variant="outline" className="border-slate-200 text-slate-600">Corte {selected.last_closed_period}</Badge></CardHeader><CardContent><div className="h-[360px] w-full"><ResponsiveContainer width="100%" height="100%"><LineChart data={chart} margin={{ top: 8, right: 12, left: 0, bottom: 8 }}><CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0"/><XAxis dataKey="period" tick={{ fontSize: 11, fill: "#64748b" }} minTickGap={22}/><YAxis tick={{ fontSize: 11, fill: "#64748b" }} tickFormatter={(v) => fmt.format(v)} width={68}/><Tooltip formatter={(value) => fmt.format(Number(value))}/><Legend/><ReferenceLine x={selected.last_closed_period} stroke="#f59e0b" strokeDasharray="4 4" label={{ value: "Corte", fill: "#b45309", fontSize: 11 }}/><Line type="monotone" dataKey="actual" name="Real" stroke="#175cd3" strokeWidth={2.5} dot={false} connectNulls={false}/><Line type="monotone" dataKey="forecast" name="Forecast estadístico" stroke="#7c3aed" strokeWidth={2.5} strokeDasharray="7 5" dot={false} connectNulls={false}/></LineChart></ResponsiveContainer></div></CardContent></Card>

      <section className="grid gap-4 xl:grid-cols-[1.15fr_.85fr]"><Card className="border-slate-200 shadow-sm"><CardHeader><CardTitle className="text-base">Comparación de modelos</CardTitle></CardHeader><CardContent className="p-0"><div className="overflow-x-auto"><Table><TableHeader><TableRow className="bg-slate-50"><TableHead>Modelo</TableHead><TableHead>WAPE</TableHead><TableHead>Sesgo</TableHead><TableHead>Estabilidad</TableHead><TableHead>Selección</TableHead></TableRow></TableHeader><TableBody>{selected.comparisons?.slice(0, 7).map((row) => <TableRow key={row.model}><TableCell className="font-medium">{row.model}</TableCell><TableCell>{row.wape.toFixed(1)}%</TableCell><TableCell><span className={`inline-flex items-center gap-1 ${row.bias > 0 ? "text-amber-700" : "text-blue-700"}`}>{row.bias > 0 ? <ArrowUpRight className="size-3.5"/> : <ArrowDownRight className="size-3.5"/>}{row.bias.toFixed(1)}%</span></TableCell><TableCell>{row.stability.toFixed(1)}%</TableCell><TableCell>{row.model === selected.winner ? <Badge className="bg-emerald-600"><CheckCircle2 className="mr-1 size-3"/> Ganador</Badge> : <span className="text-xs text-slate-500">Score {row.score.toFixed(1)}</span>}</TableCell></TableRow>)}</TableBody></Table></div></CardContent></Card>
        <div className="space-y-4"><Card className="border-slate-200 shadow-sm"><CardHeader><CardTitle className="flex items-center gap-2 text-base"><GitCompareArrows className="size-4 text-blue-700"/> Por qué ganó</CardTitle></CardHeader><CardContent><p className="text-sm leading-6 text-slate-600">{selected.explanation}</p><div className="mt-4 rounded-xl bg-slate-50 p-3 text-xs leading-5 text-slate-600">Se probaron horizontes 1, 3, 6 y 12. Cada predicción sólo vio meses anteriores a su origen; no hay fuga de información.</div></CardContent></Card><Card className="border-slate-200 shadow-sm"><CardHeader><CardTitle className="text-base">Alertas estadísticas</CardTitle></CardHeader><CardContent className="space-y-3">{selected.alerts?.length ? selected.alerts.map((alert) => <div key={alert.type} className="rounded-xl border border-amber-200 bg-amber-50 p-3"><p className="text-sm font-semibold text-amber-900">{alert.type}</p><p className="mt-1 text-xs leading-5 text-amber-800">{alert.message}</p></div>) : <div className="flex items-center gap-2 text-sm text-emerald-700"><CheckCircle2 className="size-4"/> Sin alertas estadísticas activas.</div>}</CardContent></Card></div></section>

      <Card className="border-slate-200 shadow-sm"><CardHeader className="flex flex-row items-center justify-between"><div><CardTitle className="text-base">Baseline estadístico · 12 meses</CardTitle><p className="mt-1 text-sm text-slate-500">Comparador del Forecast Towell final · versión {data.run.version}</p></div><RefreshCw className="size-4 text-slate-400"/></CardHeader><CardContent className="p-0"><div className="overflow-x-auto"><Table><TableHeader><TableRow className="bg-slate-50">{selected.forecast?.map((point) => <TableHead key={point.period} className="min-w-24 text-center">{point.period}</TableHead>)}</TableRow></TableHeader><TableBody><TableRow>{selected.forecast?.map((point) => <TableCell key={point.period} className="text-center font-semibold text-violet-700">{fmt.format(point.forecast ?? 0)}</TableCell>)}</TableRow></TableBody></Table></div></CardContent></Card>
    </>}
  </div>;
}
