"use client";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { PreviewReadError, type PreviewJob } from "@/lib/forecast-preview";
import { defaultVisibility, quantity, scopeHorizons, traderPoints, type CustomerMonth, type HistoricalMonth, type SeriesVisibility } from "@/lib/forecast-chart-data";
import { ForecastTraderChart } from "@/components/forecast/forecast-trader-chart";
import { EngineComparison } from "@/components/forecast/engine-comparison";
import { ForecastGrid, ForecastHorizonTable, ExecutiveComparison } from "@/components/forecast/forecast-horizon-table";
import { ForecastSummary, humanStatus } from "@/components/forecast/forecast-summary";
import { useForecastFilters } from "./forecast-towell-app";

type View = { key: string; result: PreviewJob | null; job: PreviewJob | null; error: string; loading: boolean };
const safeError = (error: unknown) => error instanceof PreviewReadError ? error.code : "DATA_READ_FAILED";
const stages = ["QUEUED", "READING_DATA", "ELIGIBILITY", "STATISTICAL", "ML", "ENSEMBLE", "QUALITY_GATE"];
const labels = ["En cola", "Datos", "Elegibilidad", "Estadístico", "Machine Learning", "Ensamble", "Quality Gate"];
export function ForecastEnginesView({ scope, onVisit }: { scope: string; onVisit?: () => void }) {
  const { repository, filters, profile, seriesVisibility, setSeriesVisibility } = useForecastFilters();
  const client = repository.previews;
  const [localVisibility, setLocalVisibility] = useState<SeriesVisibility>({ ...defaultVisibility });
  const visible = seriesVisibility ?? localVisibility, setVisible = setSeriesVisibility ?? setLocalVisibility;
  const [view, setView] = useState<View | null>(null);
  const [history, setHistory] = useState<{ key: string; rows: HistoricalMonth[]; customer: CustomerMonth[]; categoryName: string | null; error: boolean; customerError: boolean } | null>(null);
  const key = `${filters.chainId}/${filters.productId}`;
  const revision = useRef(0), running = useRef(false);
  const visited = useRef(onVisit);
  useEffect(() => { visited.current?.(); return () => { revision.current += 1; }; }, []);
  useEffect(() => {
    let alive = true;
    const version = ++revision.current;
    running.current = false;
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
    const version = revision.current, id = jobId;
    async function poll() {
      try {
        const job = await client!.status(id);
        const result = job.status === "READY_PREVIEW" ? await client!.result(id, filters.productId) : null;
        if (!alive || version !== revision.current) return;
        setView(previous => previous?.key === key ? { ...previous, job, result: result ?? previous.result, error: "", loading: false } : previous);
        if (!["READY_PREVIEW", "FAILED"].includes(job.status)) timer = setTimeout(poll, 2500);
      } catch (error) { if (alive && version === revision.current) setView(previous => previous?.key === key ? { ...previous, error: safeError(error) } : previous); }
    }
    timer = setTimeout(poll, 1500);
    return () => { alive = false; clearTimeout(timer); };
  }, [client, jobId, status, key, filters.productId]);
  async function run() {
    if (!client || running.current) return;
    running.current = true;
    const version = ++revision.current;
    setView(previous => ({ key, job: null, result: previous?.key === key ? previous.result : null, error: "", loading: true }));
    try { const job = await client.create(filters.chainId, filters.productId); if (revision.current === version) setView(previous => ({ key, job, result: previous?.key === key ? previous.result : null, error: "", loading: false })); }
    catch (error) { if (revision.current === version) setView(previous => ({ key, job: null, result: previous?.key === key ? previous.result : null, error: safeError(error), loading: false })); }
    finally { if (revision.current === version) running.current = false; }
  }
  const busy = current?.loading || Boolean(current?.job && !["READY_PREVIEW", "FAILED"].includes(current.job.status));
  const scopes = current?.result?.scopes ?? current?.job?.scopes ?? [];
  const selected = filters.chainId ? scopes.find(s => s.chain_id === filters.chainId) ?? null : null;
  const cutoff = selected?.issue_period;
  const historyKey = `${JSON.stringify(filters)}/${cutoff ?? ""}`;
  useEffect(() => {
    let alive = true;
    if (!filters.chainId) return;
    const readHistory = repository.getForecastHistory?.(filters) ?? Promise.resolve([]);
    const readCustomer = cutoff && repository.getCustomerForecast ? repository.getCustomerForecast(filters, cutoff) : Promise.resolve([]);
    const readCategories = filters.categoryId && repository.getCategories ? repository.getCategories(filters.chainId) : Promise.resolve([]);
    void Promise.allSettled([readHistory, readCustomer, readCategories]).then(([h, c, categories]) => { if (alive) setHistory({ key: historyKey, rows: h.status === "fulfilled" ? h.value : [], customer: c.status === "fulfilled" ? c.value : [], categoryName: categories.status === "fulfilled" ? categories.value.find(category => category.id === filters.categoryId && category.chain_id === filters.chainId)?.name ?? null : null, error: h.status === "rejected", customerError: c.status === "rejected" }); });
    return () => { alive = false; };
  }, [repository, filters, cutoff, historyKey]);
  const actual = history?.key === historyKey ? history : null;
  const horizons = scopeHorizons(selected, filters);
  const points = traderPoints(actual?.rows ?? [], actual?.customer ?? [], horizons, cutoff, filters);
  const product = selected?.products?.find(p => p.product_id === filters.productId);
  const previous = Boolean(busy && current?.result || current?.result && current.job?.status === "FAILED");
  return <section className="space-y-5">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><h1 className="text-2xl font-semibold tracking-tight">Motores de Forecast</h1><p className="mt-2 text-sm text-slate-500">{scope}{filters.categoryId && ` · ${actual?.categoryName ?? "Categoría seleccionada"}`} · {product?.description ?? "Todos los productos"} · Venta</p><p className="mt-1 text-xs text-slate-500">Datos reales hasta: {selected?.latest_actual_period ?? cutoff ?? "corte independiente por scope"} · Preview retrospectivo</p></div><Button onClick={() => void run()} disabled={!client || profile.global_role === "VIEWER" || busy}>{busy ? "Calculando vista previa…" : "Calcular vista previa"}</Button></div>
    <p className="border-l-2 border-blue-200 pl-3 text-xs leading-5 text-slate-500">Vista previa retrospectiva. Los datos históricos están certificados en valor, pero no en fecha original de disponibilidad; las métricas no constituyen certificación point-in-time.</p>
    {profile.global_role === "VIEWER" && <p className="text-sm text-slate-500">Sólo consulta: no tienes permiso para iniciar corridas.</p>}
    {!client && <p className="text-sm text-slate-500">Conexión operacional pendiente de configuración.</p>}
    {current?.error && <p role="alert" className="text-sm text-rose-700">No fue posible completar la consulta operacional: {current.error}</p>}
    {current?.job && <div role="status" className="rounded-xl border bg-white p-3 text-sm"><span>{humanStatus(current.job.status)}</span>{busy && <ol className="mt-3 flex flex-wrap gap-3 text-xs">{labels.map((label, i) => <li key={label} className={i === stages.indexOf(current.job!.status) ? "font-semibold text-blue-700" : "text-slate-500"}>{i < stages.indexOf(current.job!.status) ? "✓" : i === stages.indexOf(current.job!.status) ? "●" : "○"} {label}</li>)}</ol>}</div>}
    {!current?.job && !current?.loading && !current?.error && <p className="text-sm text-slate-500">Sin vista previa calculada</p>}
    {filters.chainId ? <>
      <ForecastSummary scope={selected} horizons={horizons}/>
      {product && <p className="text-sm text-slate-500">{product.description} · {humanStatus(product.forecast_status)}{!horizons.length && ` · Sin forecast elegible: ${product.forecast_status}`}</p>}
      {actual?.error && <p role="alert" className="text-sm text-rose-700">No fue posible consultar el histórico para la gráfica.</p>}
      {!actual && <p className="text-xs text-slate-500">Consultando histórico…</p>}
      {actual?.customerError && <p className="text-xs text-slate-500">Fcst Cliente: consulta no disponible. No se sustituyen datos ausentes.</p>}
      {filters.search && !filters.productId && <p className="text-xs text-slate-500">El histórico refleja la búsqueda. Selecciona un producto para comparar su forecast; E2 no entrega un agregado de la búsqueda.</p>}
      <ForecastTraderChart points={points} cutoff={cutoff} visible={visible} onChange={series => setVisible(v => ({ ...v, [series]: !v[series] }))} previous={previous}/>
      {selected && <EngineComparison scope={selected}/>}
      <ExecutiveComparison points={points}/>
      <ForecastHorizonTable horizons={horizons} product={Boolean(filters.productId)}/>
    </> : <>
      <p className="rounded-xl border bg-white p-5 text-sm text-slate-500">Selecciona una cadena para visualizar la evolución temporal y el pronóstico.</p>
      {current?.job?.cuts_status === "CUTS_NOT_ALIGNED" && <p className="inline-block rounded-full bg-blue-50 px-3 py-1 text-xs text-blue-800">Cortes diferentes por cadena · CUTS_NOT_ALIGNED</p>}
      {scopes.length > 0 && <ForecastGrid headers={["Cadena", "Corte", "Evaluados", "Stat", "ML", "Preview Leader", "Estado"]} rows={scopes.map(s => [s.chain_name ?? s.chain_id, s.issue_period ?? "—", quantity(s.eligibility?.evaluated), quantity(s.eligibility?.stat_eligible), quantity(s.eligibility?.ml_eligible), s.selection?.preview_leader?.model ?? s.selection?.preview_leader?.strategy ?? "—", s.error_code ?? humanStatus(s.status)])}/>}
    </>}
  </section>;
}
