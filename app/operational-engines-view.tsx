"use client";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { PreviewReadError, type PreviewJob } from "@/lib/forecast-preview";
import { useForecastFilters } from "./forecast-towell-app";

const n = (value: number | null | undefined) => value == null ? "Sin evidencia" : value.toLocaleString("es-MX", { maximumFractionDigits: 2 });
type View = { key: string; result: PreviewJob | null; job: PreviewJob | null; error: string; loading: boolean };
const safeError = (error: unknown) => error instanceof PreviewReadError ? error.code : "DATA_READ_FAILED";
function GridTable({ headers, rows }: { headers: string[]; rows: (string | number)[][] }) {
  return <div className="mt-4 overflow-auto rounded-xl border bg-white"><Table><TableHeader><TableRow>{headers.map(title => <TableHead key={title}>{title}</TableHead>)}</TableRow></TableHeader><TableBody>{rows.map((row, index) => <TableRow key={index}>{row.map((value, column) => <TableCell key={column}>{value}</TableCell>)}</TableRow>)}</TableBody></Table></div>;
}
export function ForecastEnginesView({ scope }: { scope: string }) {
  const { repository, filters, profile } = useForecastFilters();
  const client = repository.previews;
  const [view, setView] = useState<View | null>(null);
  const key = `${filters.chainId}/${filters.productId}`;
  const revision = useRef(0);
  useEffect(() => () => { revision.current += 1; }, []);
  useEffect(() => {
    let alive = true;
    const version = ++revision.current;
    if (!client) return;
    void client.latest(filters.chainId, filters.productId).then(result => {
      if (alive && revision.current === version) setView({ key, result: result?.status === "READY_PREVIEW" ? result : null, job: result, error: "", loading: false });
    }).catch(error => { if (alive && revision.current === version) setView({ key, result: null, job: null, error: safeError(error), loading: false }); });
    return () => { alive = false; };
  }, [client, filters.chainId, filters.productId, key]);
  const current = view?.key === key ? view : null;
  const jobId = current?.job?.job_id, status = current?.job?.status;
  useEffect(() => {
    if (!client || !jobId || !status || ["READY_PREVIEW", "FAILED"].includes(status)) return;
    let alive = true; let timer: ReturnType<typeof setTimeout>;
    const id = jobId;
    async function poll() {
      try {
        const job = await client!.status(id);
        const result = job.status === "READY_PREVIEW" ? await client!.result(id, filters.productId) : null;
        if (!alive) return;
        setView({ key, job, result, error: "", loading: false });
        if (!["READY_PREVIEW", "FAILED"].includes(job.status)) timer = setTimeout(poll, 2500);
      } catch (error) { if (alive) setView(previous => previous?.key === key ? { ...previous, error: safeError(error) } : previous); }
    }
    timer = setTimeout(poll, 1500);
    return () => { alive = false; clearTimeout(timer); };
  }, [client, jobId, status, key, filters.productId]);
  async function run() {
    if (!client) return;
    const version = ++revision.current;
    setView({ key, job: null, result: null, error: "", loading: true });
    try { const job = await client.create(filters.chainId, filters.productId); if (revision.current === version) setView({ key, job, result: null, error: "", loading: false }); }
    catch (error) { if (revision.current === version) setView({ key, job: null, result: null, error: safeError(error), loading: false }); }
  }
  const busy = current?.loading || Boolean(current?.job && !["READY_PREVIEW", "FAILED"].includes(current.job.status));
  const scopes = current?.result?.scopes ?? current?.job?.scopes ?? [];
  const selected = scopes.length === 1 ? scopes[0] : null;
  const products = (selected?.products ?? []).filter(product => (!filters.categoryId || product.category_id === filters.categoryId)
    && (!filters.productId || product.product_id === filters.productId)
    && (filters.productId || !filters.search || `${product.description} ${product.product_code}`.toLowerCase().includes(filters.search.toLowerCase())));
  const statLeader = Object.entries(selected?.statistical?.models ?? {}).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0]?.[0];
  return <section>
    <div className="mb-5 flex flex-wrap items-start justify-between gap-3"><div><h1 className="text-2xl font-semibold">Motores de Forecast</h1><p className="mt-2 text-sm text-slate-500">{scope} · Venta · RETROSPECTIVE PREVIEW</p><p className="mt-1 text-sm text-slate-500">Datos reales hasta: {selected?.issue_period ?? "corte independiente por scope"} · H1 → H12</p><p className="mt-1 text-xs text-slate-500">{current?.result?.engine_version ?? "Versión disponible tras la corrida"}</p></div><Button onClick={() => void run()} disabled={!client || profile.global_role === "VIEWER" || busy}>{busy ? "Calculando vista previa…" : "Calcular vista previa"}</Button></div>
    <p className="mb-5 rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900">Vista previa retrospectiva. El histórico tiene valores certificados, pero no dispone de fechas originales de disponibilidad; por ello las métricas mostradas no constituyen certificación point-in-time. Los periodos históricos no cambian el corte operacional.</p>
    {profile.global_role === "VIEWER" && <p className="mb-3 text-sm text-slate-500">Sólo consulta: no tienes permiso para iniciar corridas.</p>}
    {!client && <p className="mb-3 text-sm text-slate-500">Conexión operacional pendiente de configuración.</p>}
    {current?.error && <p role="alert" className="mb-4 text-sm text-rose-700">No fue posible completar la consulta operacional: {current.error}</p>}
    {current?.job && <p role="status" className="mb-4 text-sm">Estado: {current.job.status} {current.job.cuts_status === "CUTS_NOT_ALIGNED" && "· CUTS_NOT_ALIGNED — no se consolida entre scopes"}</p>}
    <p className="mb-3 text-xs text-slate-500">Última corrida: {current?.job?.created_at ? new Date(current.job.created_at * 1000).toLocaleString("es-MX") : "Sin corrida"} · Producto: {products.find(product => product.product_id === filters.productId)?.description ?? "Todos los productos"}</p>
    <div className="grid gap-4 lg:grid-cols-3">
      <Card><CardContent className="p-5"><h2 className="font-semibold">Motor Estadístico</h2><p>Estado: {selected?.statistical?.status ?? (scopes.length > 1 ? "Consultar cobertura por scope" : "Sin vista previa")}</p><p>Productos evaluados: {n(selected?.eligibility?.evaluated)}</p><p>Elegibles: {n(selected?.eligibility?.stat_eligible)}</p><p>Modelos candidatos: {selected?.statistical?.available_candidates?.length ?? new Set(selected?.statistical?.candidates.map(item => item.model)).size}</p><p>Modelo líder por cobertura: {statLeader ?? "Sin candidato elegible"}</p><p>WAPE retrospectivo: {n(selected?.statistical?.retrospective_wape)}%</p><p>Bias retrospectivo: {n(selected?.statistical?.retrospective_bias)}%</p><details className="mt-3"><summary>Ver detalle</summary>{Object.entries(selected?.statistical?.models ?? {}).map(([model, count]) => <p key={model}>{model}: {count}</p>)}</details></CardContent></Card>
      <Card><CardContent className="p-5"><h2 className="font-semibold">Machine Learning</h2><p>Estado: {selected?.ml?.status ?? "Sin vista previa"}</p><p>Muestras de entrenamiento: {n(selected?.ml?.training_samples)}</p><p>Elegibles: {n(selected?.eligibility?.ml_eligible)}</p><p>Candidate leader: {selected?.ml?.leader ?? "Sin candidato elegible"}</p><p>WAPE retrospectivo: {n(selected?.ml?.retrospective_wape)}%</p><p>Bias retrospectivo: {n(selected?.ml?.retrospective_bias)}%</p><details className="mt-3"><summary>Ver detalle ML</summary>{(selected?.ml?.available_candidates ?? selected?.ml?.candidates.map(item => item.model ?? "") ?? []).map(model => <p key={model}>{model} · WAPE {n(selected?.ml?.candidates.find(item => item.model === model)?.retrospective_validation_wape)}%</p>)}</details></CardContent></Card>
      <Card><CardContent className="p-5"><h2 className="font-semibold">Champion / Challenger</h2><p>Estado: PREVIEW — NO PUBLICADO</p><p>Champion publicado: {selected?.selection?.published_champion?.version ?? "Sin Champion publicado"}</p><p>Candidate leader: {selected?.selection?.preview_leader?.strategy ?? "Sin evidencia"}</p><p>Challenger: {selected?.selection?.preview_challenger?.strategy ?? "Sin evidencia"}</p><p>No Degradation: {selected?.selection?.no_degradation == null ? "N/A" : selected.selection.no_degradation ? "PASS" : "FAIL"}</p><p>Certificación: {selected ? "PROVISIONAL_TEMPORAL_UNKNOWN" : "Sin evidencia"}</p><p>Automatic promotion: OFF</p></CardContent></Card>
    </div>
    {scopes.length > 0 && <GridTable headers={["Scope", "Corte", "Productos", "Stat", "ML", "Preview leader", "WAPE retro", "Status"]} rows={scopes.map(item => [item.chain_name ?? item.chain_id, item.issue_period ?? "Sin corte", n(item.eligibility?.visible_products), n(item.eligibility?.stat_eligible), n(item.eligibility?.ml_eligible), item.selection?.preview_leader?.strategy ?? "Sin candidato", n(item.statistical?.retrospective_wape), item.error_code ?? item.status])}/>}
    {selected?.eligibility && <p className="mt-4 text-sm text-slate-500">Cold start: {n(selected.eligibility.COLD_START)} · Inactive: {n(selected.eligibility.INACTIVE)} · Pre-launch: {n(selected.eligibility["PRE-LAUNCH"])} · Insufficient: {n(selected.eligibility.INSUFFICIENT)}</p>}
    <h2 className="mt-6 font-semibold">Forecast Towell H1–H12</h2>
    {!filters.productId && <p className="mt-2 text-sm text-slate-500">Selecciona un producto para consultar sus doce horizontes.</p>}
    {filters.productId && products.map(product => <div key={product.product_id} className="mt-3"><p className="text-sm">{product.description} · {product.forecast_status}</p>{!product.horizons?.length ? <p className="p-4 text-sm">Sin forecast elegible: {product.forecast_status}</p> : <GridTable headers={["Horizonte", "Periodo", "Estadístico", "ML", "Fcst Towell", "P10", "P50", "P90", "P95"]} rows={product.horizons.map(row => [`H${row.horizon}`, row.target_period, ...[row.statistical_value, row.ml_value, row.forecast_towell, row.p10, row.p50, row.p90, row.p95].map(n)])}/>}</div>)}
  </section>;
}
