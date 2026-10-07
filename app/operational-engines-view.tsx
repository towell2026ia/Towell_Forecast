"use client";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { PreviewReadError } from "@/lib/forecast-preview";
import { defaultVisibility, quantity, scopeHorizons, scopeProvisionalHorizons, traderPoints, type CustomerMonth, type HistoricalMonth, type SeriesVisibility } from "@/lib/forecast-chart-data";
import { ForecastTraderChart } from "@/components/forecast/forecast-trader-chart";
import { EngineComparison } from "@/components/forecast/engine-comparison";
import { ForecastGrid, ForecastHorizonTable, ExecutiveComparison } from "@/components/forecast/forecast-horizon-table";
import { ForecastSummary, humanStatus } from "@/components/forecast/forecast-summary";
import { ForecastEnginesTabs, type EngineTab } from "@/components/forecast/forecast-engines-tabs";
import { MLEngineDetail } from "@/components/forecast/ml-engine-detail";
import { StatisticalEngineDetail } from "@/components/forecast/statistical-engine-detail";
import { useForecastFilters, type PreviewView } from "./forecast-towell-app";
import { currentRetrospectivePreview } from "@/lib/preview-presentation";
import type { VintageDetail, VintageSummary } from "@/lib/forecast-preview";

const safeError = (error: unknown) => error instanceof PreviewReadError ? error.code : "DATA_READ_FAILED";
const researchFailure = (reason?: string) => ({
  AUTHENTICATION: "OpenAI rechazó la autenticación. Revisa la clave configurada en Railway.",
  QUOTA: "La cuenta de API no tiene cuota o saldo disponible.",
  RATE_LIMIT: "OpenAI limitó temporalmente las solicitudes. Intenta más tarde.",
  MODEL_ACCESS: "El proyecto de API no tiene acceso al modelo configurado.",
  TIMEOUT: "La consulta excedió el tiempo de espera.",
  NETWORK: "Railway no pudo completar la conexión con OpenAI.",
  INCOMPLETE: "La investigación terminó sin respuesta completa.",
  EMPTY_RESPONSE: "La investigación terminó sin texto verificable.",
  RESPONSE_FAILED: "OpenAI no completó la investigación.",
  REQUEST_REJECTED: "OpenAI rechazó la solicitud de investigación.",
  PROVIDER_ERROR: "OpenAI presentó un error temporal.",
} as Record<string, string>)[reason ?? ""] ?? "No se pudo determinar el motivo de la consulta anterior. Una nueva corrida permitirá diagnosticarla.";
const stages = ["QUEUED", "READING_DATA", "ELIGIBILITY", "STATISTICAL", "ML", "ENSEMBLE", "QUALITY_GATE"];
const labels = ["En cola", "Datos", "Elegibilidad", "Estadístico", "Machine Learning", "Ensamble", "Quality Gate"];
export function ForecastEnginesView({ scope, onVisit }: { scope: string; onVisit?: () => void }) {
  const { repository, filters, profile, seriesVisibility, setSeriesVisibility, preview: sharedPreview, setPreview: sharedSetPreview } = useForecastFilters();
  const client = repository.previews;
  const [localVisibility, setLocalVisibility] = useState<SeriesVisibility>({ ...defaultVisibility });
  const [tab, setTab] = useState<EngineTab>("summary");
  const [localPreview, setLocalPreview] = useState<PreviewView | null>(null);
  const setPreview = sharedSetPreview ?? setLocalPreview;
  const [compareTowell, setCompareTowell] = useState(false);
  const visible = seriesVisibility ?? localVisibility, setVisible = setSeriesVisibility ?? setLocalVisibility;
  const [history, setHistory] = useState<{ key: string; rows: HistoricalMonth[]; customer: CustomerMonth[]; categoryName: string | null; error: boolean; customerError: boolean } | null>(null);
  const key = `${filters.chainId}/${filters.productId}`;
  const current = (sharedPreview === undefined ? localPreview : sharedPreview)?.key === key ? (sharedPreview === undefined ? localPreview : sharedPreview) : null;
  const revision = useRef(0), running = useRef(false);
  const visited = useRef(onVisit);
  useEffect(() => { visited.current?.(); return () => { revision.current += 1; }; }, []);
  // Standalone component fixtures retain their read path; the application root owns the shared read.
  useEffect(() => {
    if (sharedPreview !== undefined || !client) return;
    let alive = true;
    void client.latest(filters.chainId, filters.productId).then(job => {
      if (alive) setLocalPreview({ key, result: ["READY_PREVIEW", "READY_PROVISIONAL"].includes(job?.status ?? "") ? job : null, job, error: "", loading: false });
    }).catch(error => { if (alive) setLocalPreview({ key, result: null, job: null, error: safeError(error), loading: false }); });
    return () => { alive = false; };
  }, [sharedPreview, client, filters.chainId, filters.productId, key]);
  useEffect(() => { revision.current += 1; running.current = false; }, [key]);
  const jobId = current?.job?.job_id, status = current?.job?.status;
  useEffect(() => {
    if (!client || !jobId || !status || ["READY_PREVIEW", "READY_PROVISIONAL", "NOT_ELIGIBLE", "FAILED"].includes(status)) return;
    let alive = true; let timer: ReturnType<typeof setTimeout>;
    const version = revision.current, id = jobId;
    async function poll() {
      try {
        const job = await client!.status(id);
        const result = ["READY_PREVIEW", "READY_PROVISIONAL"].includes(job.status) ? await client!.result(id, filters.productId) : null;
        if (!alive || version !== revision.current) return;
        setPreview(previous => previous?.key === key ? { ...previous, job, result: ["READY_PREVIEW", "READY_PROVISIONAL"].includes(job.status) ? result ?? previous.result : ["NOT_ELIGIBLE", "FAILED"].includes(job.status) ? null : previous.result, error: "", loading: false } : previous);
        if (!["READY_PREVIEW", "READY_PROVISIONAL", "NOT_ELIGIBLE", "FAILED"].includes(job.status)) timer = setTimeout(poll, 2500);
      } catch (error) { if (alive && version === revision.current) setPreview(previous => previous?.key === key ? { ...previous, error: safeError(error) } : previous); }
    }
    timer = setTimeout(poll, 1500);
    return () => { alive = false; clearTimeout(timer); };
  }, [client, jobId, status, key, filters.productId, setPreview]);
  async function run() {
    if (!client || running.current) return;
    running.current = true;
    const version = ++revision.current;
    setPreview(previous => ({ key, job: null, result: previous?.key === key ? previous.result : null, error: "", loading: true }));
    try { const job = await client.create(filters.chainId, filters.productId); if (revision.current === version) setPreview(previous => ({ key, job, result: previous?.key === key ? previous.result : null, error: "", loading: false })); }
    catch (error) { if (revision.current === version) setPreview(previous => ({ key, job: null, result: previous?.key === key ? previous.result : null, error: safeError(error), loading: false })); }
    finally { if (revision.current === version) running.current = false; }
  }
  const busy = current?.loading || Boolean(current?.job && !["READY_PREVIEW", "READY_PROVISIONAL", "NOT_ELIGIBLE", "FAILED"].includes(current.job.status));
  const scopes = current?.job && ["NOT_ELIGIBLE", "FAILED"].includes(current.job.status) ? current.job.scopes : current?.result?.scopes ?? current?.job?.scopes ?? [];
  const selected = filters.chainId ? scopes.find(s => s.chain_id === filters.chainId) ?? null : null;
  const provisional = selected?.provisional_cold_start;
  useEffect(() => {
    if (!provisional || provisional.research?.status !== "PENDING" || !client?.research || !jobId) return;
    let alive = true;
    const timer = setTimeout(() => { void client.research!(jobId).then(result => {
      if (alive) setPreview(previous => previous?.key === key && previous.job?.job_id === jobId
        ? { ...previous, job: result, result, error: "" } : previous);
    }).catch(() => { /* Research is optional; numeric estimate remains available. */ }); }, 8000);
    return () => { alive = false; clearTimeout(timer); };
  }, [provisional, client, jobId, key, setPreview]);
  const stale = Boolean(current?.result && selected?.status === "PREVIEW" && !currentRetrospectivePreview(current.result, selected));
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
  const horizons = scopeHorizons(stale ? null : selected, filters);
  const provisionalHorizons = scopeProvisionalHorizons(selected, filters);
  const points = traderPoints(actual?.rows ?? [], actual?.customer ?? [], horizons, cutoff, filters, provisionalHorizons);
  const product = selected?.products?.find(p => p.product_id === filters.productId);
  const noEligible = !provisional && (selected?.error_code === "NO_ELIGIBLE_PRODUCTS" || Boolean(filters.productId && product &&
    ["INSUFFICIENT", "COLD_START", "PRE-LAUNCH", "INACTIVE"].includes(product.forecast_status) && !product.horizons?.length));
  const previous = Boolean(busy && current?.result);
  return <section className="space-y-5">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><h1 className="text-2xl font-semibold tracking-tight">Motores de Forecast</h1><p className="mt-2 text-sm text-slate-500">{scope}{filters.categoryId && ` · ${actual?.categoryName ?? "Categoría seleccionada"}`} · {product?.description ?? "Todos los productos"} · Venta</p><p className="mt-1 text-xs text-slate-500">Datos reales hasta: {selected?.latest_actual_period ?? cutoff ?? "corte independiente por scope"} · Preview retrospectivo</p></div><Button onClick={() => void run()} disabled={!client || profile.global_role === "VIEWER" || busy}>{busy ? "Calculando vista previa…" : "Calcular vista previa"}</Button></div>
    <p className="border-l-2 border-blue-200 pl-3 text-xs leading-5 text-slate-500">Vista previa retrospectiva. Los datos históricos están certificados en valor, pero no en fecha original de disponibilidad; las métricas no constituyen certificación point-in-time.</p>
    {profile.global_role === "VIEWER" && <p className="text-sm text-slate-500">Sólo consulta: no tienes permiso para iniciar corridas.</p>}
    {!client && <p className="text-sm text-slate-500">Conexión operacional pendiente de configuración.</p>}
    {current?.error && <p role="alert" className="text-sm text-rose-700">No fue posible completar la consulta operacional: {current.error}</p>}
    {current?.job && <div role="status" className="rounded-xl border bg-white p-3 text-sm"><span>{noEligible ? "Historial insuficiente" : humanStatus(current.job.status)}</span>{busy && <ol className="mt-3 flex flex-wrap gap-3 text-xs">{labels.map((label, i) => <li key={label} className={i === stages.indexOf(current.job!.status) ? "font-semibold text-blue-700" : "text-slate-500"}>{i < stages.indexOf(current.job!.status) ? "✓" : i === stages.indexOf(current.job!.status) ? "●" : "○"} {label}</li>)}</ol>}</div>}
    {noEligible && <p role="status" className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">{product?.forecast_status === "INSUFFICIENT" && product.history_months !== undefined
      ? `${product.description}: ${product.history_months} meses de venta consecutivos después de su primera venta; el pronóstico validado requiere ${product.minimum_history_months ?? 6}. No se ejecutaron los modelos estadísticos ni ML para este producto. Una estimación cold start necesitará validación separada antes de mostrarse.`
      : product ? `${product.description}: ${humanStatus(product.forecast_status)}. No hay pronóstico validado ni métricas retrospectivas de este producto.`
      : "Ningún producto de este alcance cumple todavía los mínimos de historial para un pronóstico validado. No se generaron cifras."}</p>}
    {current?.job?.status === "FAILED" && selected?.error_code && selected.error_code !== "NO_ELIGIBLE_PRODUCTS" && <p role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">El cálculo no se completó. Código: {selected.error_code}. No se publicó ningún pronóstico.</p>}
    {!current?.job && !current?.loading && !current?.error && <p className="text-sm text-slate-500">Sin vista previa calculada</p>}
    {stale && <p className="rounded-xl border bg-amber-50 p-3 text-xs text-amber-800">Vista previa desactualizada. Sus métricas retrospectivas no se muestran; el cálculo no se ejecuta automáticamente.</p>}
    {provisional && <section className="space-y-3 rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm" aria-label="Estimación provisional cold start">
      <h2 className="font-semibold text-blue-950">Estimación provisional de arranque · {provisional.observed_months} meses observados</h2>
      <p>Líder provisional: {provisional.model === "ml_random_forest" ? "Random Forest global de comparables" : provisional.model === "analog" ? "Trayectoria de comparables" : "Nivel base de comparables"}. Confianza: {provisional.confidence === "LOW" ? "baja" : "limitada"}. No es Fcst Towell oficial ni Champion.</p>
      <p>{provisional.comparables.length} productos comparables del mismo scope y categoría. El WAPE siguiente pertenece a su validación retrospectiva, no a {product?.description ?? "este producto nuevo"}.</p>
      <div className="overflow-x-auto"><table className="min-w-full text-left"><thead><tr><th className="p-2">Método</th><th className="p-2">WAPE comparables</th><th className="p-2">Bias comparables</th><th className="p-2">Observaciones</th></tr></thead><tbody>{Object.entries(provisional.retrospective_peer_metrics).map(([method, metric]) => <tr key={method} className="border-t"><td className="p-2">{method}</td><td className="p-2">{metric.wape}%</td><td className="p-2">{metric.bias}%</td><td className="p-2">{metric.observations}</td></tr>)}</tbody></table></div>
      <div className="overflow-x-auto"><table className="min-w-full text-left"><thead><tr><th className="p-2">Horizonte</th><th className="p-2">Periodo</th><th className="p-2">Piezas provisionales</th></tr></thead><tbody>{provisional.horizons.map(row => <tr key={row.horizon} className="border-t"><td className="p-2">H{row.horizon}</td><td className="p-2">{row.target_period}</td><td className="p-2">{quantity(row.value)}</td></tr>)}</tbody></table></div>
      <p className="text-xs">Evaluación retrospectiva rolling-origin de productos comparables. No constituye certificación point-in-time ni mide el error futuro del producto nuevo.</p>
      <div className="border-t border-blue-200 pt-3"><h3 className="font-medium">Investigación pública complementaria</h3>{provisional.research?.status === "PENDING" ? <p>Consultando fuentes públicas…</p> : provisional.research?.status === "COMPLETED" ? <><p className="whitespace-pre-wrap">{provisional.research.summary}</p><ul>{provisional.research.sources?.map(source => <li key={source.url}><a className="underline" href={source.url} target="_blank" rel="noopener noreferrer">{source.title}</a></li>)}</ul></> : <p>{provisional.research?.status === "DISABLED" ? "No habilitada en el servidor. La estimación cuantitativa no depende de esta consulta." : `${researchFailure(provisional.research?.reason)} La estimación cuantitativa permanece independiente.`}</p>}</div>
    </section>}
    <ForecastEnginesTabs value={tab} onChange={setTab}/>
    {tab === "vintages" && <div role="tabpanel" id="engine-panel-vintages" aria-labelledby="engine-tab-vintages"><VintagesPanel key={filters.chainId ?? "all"} chainId={filters.chainId}/></div>}
    {tab === "statistical" && <div role="tabpanel" id="engine-panel-statistical" aria-labelledby="engine-tab-statistical" className="space-y-4">{provisional ? <><p className="rounded-xl border bg-white p-5 text-sm text-slate-600">El motor estadístico validado requiere más historial propio. Se grafica el líder provisional de comparables, no un pronóstico estadístico certificado ni un WAPE de este producto.</p><ForecastTraderChart points={points} cutoff={cutoff} visible={visible} onChange={series => setVisible(v => ({ ...v, [series]: !v[series] }))}/></> : noEligible ? <p className="rounded-xl border bg-white p-5 text-sm text-slate-600">No se ejecutó el motor estadístico: el producto no reúne el historial mínimo. No hay WAPE, Bias ni pronóstico que mostrar.</p> : <StatisticalEngineDetail scope={stale ? null : selected} scopes={stale ? [] : scopes} filters={filters} historical={actual?.rows ?? []} historyError={actual?.error} cutsStatus={current?.result?.cuts_status ?? current?.job?.cuts_status} compareTowell={compareTowell} onCompare={() => setCompareTowell(v => !v)}/>}</div>}
    {tab === "ml" && <div role="tabpanel" id="engine-panel-ml" aria-labelledby="engine-tab-ml" className="space-y-4">{provisional ? <><p className="rounded-xl border bg-white p-5 text-sm text-slate-600">Random Forest global, trayectoria análoga y baseline se evaluaron con comparables. La línea muestra sólo el método provisional elegido; no inventamos una línea ML separada cuando Random Forest no ganó.</p><ForecastTraderChart points={points} cutoff={cutoff} visible={visible} onChange={series => setVisible(v => ({ ...v, [series]: !v[series] }))}/></> : noEligible ? <p className="rounded-xl border bg-white p-5 text-sm text-slate-600">No se ejecutó Machine Learning para este producto. No se atribuyen métricas de otros productos a Oxford.</p> : <MLEngineDetail scope={stale ? null : selected} scopes={stale ? [] : scopes} filters={filters} historical={actual?.rows ?? []} horizons={horizons} historyError={actual?.error} cutsStatus={current?.result?.cuts_status ?? current?.job?.cuts_status}/>}</div>}
    {tab === "summary" && <div role="tabpanel" id="engine-panel-summary" aria-labelledby="engine-tab-summary" className="space-y-5">{filters.chainId ? <>
      {!noEligible && !provisional && <ForecastSummary scope={selected} horizons={horizons}/>}
      {product && <p className="text-sm text-slate-500">{product.description} · {humanStatus(product.forecast_status)}{!horizons.length && !provisional && ` · Sin forecast elegible: ${product.forecast_status}`}</p>}
      {actual?.error && <p role="alert" className="text-sm text-rose-700">No fue posible consultar el histórico para la gráfica.</p>}
      {!actual && <p className="text-xs text-slate-500">Consultando histórico…</p>}
      {actual?.customerError && <p className="text-xs text-slate-500">Fcst Cliente: consulta no disponible. No se sustituyen datos ausentes.</p>}
      {filters.search && !filters.productId && <p className="text-xs text-slate-500">El histórico refleja la búsqueda. Selecciona un producto para comparar su forecast; E2 no entrega un agregado de la búsqueda.</p>}
      <ForecastTraderChart points={points} cutoff={cutoff} visible={visible} onChange={series => setVisible(v => ({ ...v, [series]: !v[series] }))} previous={previous}/>
      {selected && !stale && !noEligible && !provisional && <EngineComparison scope={selected} horizons={horizons} productId={filters.productId}/>}
      {!noEligible && !provisional && <ExecutiveComparison points={points} cutoff={cutoff}/>}
      {!noEligible && !provisional && <ForecastHorizonTable horizons={horizons} product={Boolean(filters.productId)}/>}
    </> : <>
      <p className="rounded-xl border bg-white p-5 text-sm text-slate-500">Selecciona una cadena para visualizar la evolución temporal y el pronóstico.</p>
      {current?.job?.cuts_status === "CUTS_NOT_ALIGNED" && <p className="inline-block rounded-full bg-blue-50 px-3 py-1 text-xs text-blue-800">Cortes diferentes por cadena · CUTS_NOT_ALIGNED</p>}
      {scopes.length > 0 && <ForecastGrid headers={["Cadena", "Corte", "Evaluados", "Stat", "ML", "Preview Leader", "Estado"]} rows={scopes.map(s => [s.chain_name ?? s.chain_id, s.issue_period ?? "—", quantity(s.eligibility?.evaluated), quantity(s.eligibility?.stat_eligible), quantity(s.eligibility?.ml_eligible), s.selection?.preview_leader?.model ?? s.selection?.preview_leader?.strategy ?? "—", s.error_code ?? humanStatus(s.status)])}/>}
    </>}</div>}
  </section>;
}

function VintagesPanel({ chainId }: { chainId: string | null }) {
  const { repository, profile, preview } = useForecastFilters();
  const [state, setState] = useState<{ chainId: string; rows: VintageSummary[]; flags: { vintage_persistence: boolean; official_publication: boolean; champion_publication: boolean }; error: boolean } | null>(null);
  const [revision, setRevision] = useState(0);
  const [actionError, setActionError] = useState("");
  const [actionBusy, setActionBusy] = useState(false);
  const [selectedVintage, setSelectedVintage] = useState<string | null>(null);
  const [detail, setDetail] = useState<VintageDetail | null>(null);
  const [detailError, setDetailError] = useState(false);
  const selected = chainId ? preview?.result?.scopes.find(scope => scope.chain_id === chainId) ?? null : null;
  const readyPreview = currentRetrospectivePreview(preview?.result ?? null, selected) ? selected : null;
  useEffect(() => {
    let alive = true;
    if (chainId && repository.previews?.vintages && repository.previews.vintageCapabilities) void Promise.all([
      repository.previews.vintages(chainId), repository.previews.vintageCapabilities(),
    ]).then(([rows, flags]) => { if (alive) setState({ chainId, rows, flags, error: false }); })
      .catch(() => { if (alive) setState({ chainId, rows: [], flags: { vintage_persistence: false, official_publication: false, champion_publication: false }, error: true }); });
    return () => { alive = false; };
  }, [repository, chainId, revision]);
  useEffect(() => {
    let alive = true;
    if (selectedVintage && repository.previews?.vintageDetail) void repository.previews.vintageDetail(selectedVintage)
      .then(value => { if (alive && value.vintage.chain_id === chainId) setDetail(value); else if (alive) setDetailError(true); })
      .catch(() => { if (alive) setDetailError(true); });
    return () => { alive = false; };
  }, [repository, selectedVintage, chainId, revision]);
  async function act(operation: () => Promise<string>) {
    if (actionBusy) return;
    setActionBusy(true); setActionError("");
    try { await operation(); setDetail(null); setDetailError(false); setRevision(value => value + 1); }
    catch (error) { setActionError(error instanceof PreviewReadError ? error.code : "VINTAGE_WRITE_FAILED"); }
    finally { setActionBusy(false); }
  }
  if (!chainId) return <p className="rounded-xl border bg-white p-5 text-sm text-slate-500">Selecciona una cadena para consultar sus vintages. No se mezclan scopes padre e hijo.</p>;
  if (!state || state.chainId !== chainId) return <p role="status" className="rounded-xl border bg-white p-5 text-sm text-slate-500">Consultando vintages…</p>;
  if (state.error) return <p role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-5 text-sm text-rose-700">No fue posible consultar los vintages.</p>;
  return <div className="rounded-xl border bg-white p-5"><div className="flex flex-wrap items-center justify-between gap-3"><h2 className="font-semibold">Versiones del pronóstico (vintages)</h2>{profile.global_role === "ADMIN" && state.flags.vintage_persistence && readyPreview?.preview_id && repository.previews?.createCandidate && <Button disabled={actionBusy} onClick={() => { if (window.confirm("¿Crear candidato provisional a partir del preview actual? No publica forecast oficial.")) void act(() => repository.previews!.createCandidate!(chainId, readyPreview.preview_id!)); }}>Crear candidato de vintage</Button>}</div><p className="mt-2 text-sm text-slate-500">Un vintage es una fotografía fechada del pronóstico, sus datos y modelos; permite comparar lo que se sabía entonces con el resultado real posterior. Calcular una vista previa no crea un vintage ni elige un Champion oficial.</p>{!state.flags.vintage_persistence && <p className="mt-2 text-sm text-amber-800">La conservación de versiones oficiales aún no está activada en este servicio. Por eso no aparece un Champion publicado.</p>}{profile.global_role !== "ADMIN" && <p className="mt-2 text-xs text-slate-500">Tu acceso es de consulta; las acciones gerenciales requieren autorización del backend.</p>}{actionError && <p role="alert" className="mt-3 text-sm text-rose-700">La acción no se completó: {actionError}</p>}{state.rows.length ? <div className="mt-4 overflow-x-auto"><table className="min-w-full text-left text-sm"><thead><tr className="border-b text-slate-500"><th className="p-2">Vintage</th><th className="p-2">Periodo</th><th className="p-2">Corte</th><th className="p-2">Estado</th><th className="p-2">Frozen</th><th className="p-2">Creado</th><th className="p-2">Acciones</th></tr></thead><tbody>{state.rows.map(row => <tr key={row.id} className="border-b"><td className="p-2 font-mono text-xs">{row.id}</td><td className="p-2">{row.issue_period.slice(0, 7)}</td><td className="p-2">{row.cutoff_at.slice(0, 10)}</td><td className="p-2">{row.certification_status}</td><td className="p-2">{row.frozen_at ? "Sí" : "No"}</td><td className="p-2">{row.created_at.slice(0, 10)}</td><td className="p-2 whitespace-nowrap"><Button size="sm" variant="outline" onClick={() => { setDetail(null); setDetailError(false); setSelectedVintage(value => value === row.id ? null : row.id); }}>{selectedVintage === row.id ? "Cerrar detalle" : "Ver detalle"}</Button>{profile.global_role === "ADMIN" && state.flags.vintage_persistence && !row.frozen_at && repository.previews?.freezeVintage && <Button size="sm" variant="outline" disabled={actionBusy} onClick={() => { if (window.confirm("¿Congelar este vintage? Después no podrá modificarse.")) void act(() => repository.previews!.freezeVintage!(row.id)); }}>Congelar</Button>}{profile.global_role === "ADMIN" && state.flags.official_publication && row.frozen_at && row.certification_status === "CERTIFIED" && repository.previews?.publishVintage && <Button size="sm" variant="outline" disabled={actionBusy} onClick={() => { const comment = window.prompt("Justificación para publicar forecast oficial (mínimo 5 caracteres)"); if (comment && comment.trim().length >= 5 && window.confirm("¿Confirmas la publicación oficial?")) void act(() => repository.previews!.publishVintage!(row.id, comment)); }}>Publicar oficial</Button>}</td></tr>)}</tbody></table></div> : <p className="mt-4 text-sm text-slate-500">Todavía no hay versiones guardadas para este scope.</p>}{selectedVintage && !detail && !detailError && <p role="status" className="mt-4 text-sm text-slate-500">Consultando detalle del vintage…</p>}{selectedVintage && detailError && <p role="alert" className="mt-4 text-sm text-rose-700">No fue posible consultar el detalle del vintage.</p>}{detail && detail.vintage.id === selectedVintage && detail.vintage.chain_id === chainId && <VintageDetailView detail={detail}/>}</div>;
}

function VintageDetailView({ detail }: { detail: VintageDetail }) {
  const validation = detail.metrics.filter(row => row.phase === "VALIDATION");
  const metric = (name: string) => validation.find(row => row.metric === name)?.value;
  const gate = (name: string) => detail.gates.find(row => row.gate_type === name)?.status ?? "—";
  return <div className="mt-5 space-y-4 rounded-xl border bg-slate-50 p-4 text-sm">
    <h3 className="font-semibold">Lineage y desempeño del vintage</h3>
    <dl className="grid gap-2 md:grid-cols-2"><div>Run: <span className="font-mono text-xs">{detail.run.id}</span></div><div>Actor: <span className="font-mono text-xs">{detail.run.actor_id ?? "—"}</span></div><div>Dataset SHA: <span className="break-all font-mono text-xs">{detail.run.data_snapshot_hash}</span></div><div>Git SHA: <span className="font-mono text-xs">{detail.run.git_sha}</span></div><div>Motor: {detail.run.engine_version}</div><div>Inputs: {detail.inputs.length} · Agregados: {detail.aggregates.length}</div><div>WAPE retrospectivo: {metric("WAPE") ?? "—"}</div><div>Bias retrospectivo: {metric("BIAS") ?? "—"}</div><div>Datos: {gate("DATA_QUALITY")}</div><div>Forecast: {gate("FORECAST_QUALITY")}</div><div>Servicio: {gate("SERVICE_LEVEL")}</div></dl>
    <p className="text-xs text-slate-500">Evaluación retrospectiva rolling-origin. No constituye certificación point-in-time.</p>
    <div className="overflow-x-auto"><table className="min-w-full text-left text-xs"><thead><tr className="border-b"><th className="p-2">Producto</th><th className="p-2">H</th><th className="p-2">Mes</th><th className="p-2">Stat</th><th className="p-2">ML</th><th className="p-2">Fcst Towell</th><th className="p-2">P10 / P50 / P90 / P95</th></tr></thead><tbody>{detail.horizons.map(row => <tr key={row.id} className="border-b"><td className="p-2 font-mono">{row.product_id}</td><td className="p-2">{row.horizon}</td><td className="p-2">{row.target_period.slice(0, 7)}</td><td className="p-2">{row.statistical_value ?? "—"}</td><td className="p-2">{row.ml_value ?? "—"}</td><td className="p-2">{row.forecast_towell}</td><td className="p-2">{[row.p10, row.p50, row.p90, row.p95].map(value => value ?? "—").join(" / ")}</td></tr>)}</tbody></table></div>
    <div>Modelos: {detail.models.map(row => `${row.model_family} · ${row.algorithm} (${row.certification_status})`).join("; ") || "—"}</div>
  </div>;
}
