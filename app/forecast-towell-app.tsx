"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { BrainCircuit, CalendarRange, ClipboardCheck, FileClock, History, Home, LogOut, PanelLeft, ShieldCheck, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarGroupLabel, SidebarHeader, SidebarInset, SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { logPortalEvent } from "@/lib/supabase/client";
import { resetForChain } from "@/lib/forecast-data";
import { emptyFilters, type Cursor, type Filters, type ForecastReadRepository, type Profile } from "@/lib/supabase/types";
import ForecastAssistant from "./forecast-assistant";
import ForecastAssistantErrorBoundary from "./forecast-assistant-error-boundary";
import type { AssistantContext } from "./assistant/assistant-service";
import { ForecastEnginesView } from "./operational-engines-view";
import { defaultVisibility, scopeProvisionalHorizons, type SeriesVisibility } from "@/lib/forecast-chart-data";
import { PreviewReadError, type PreviewJob } from "@/lib/forecast-preview";
import { ForecastTraderChart } from "@/components/forecast/forecast-trader-chart";
import { scopeHorizons, traderPoints } from "@/lib/forecast-chart-data";
import { currentRetrospectivePreview } from "@/lib/preview-presentation";
import { PreviewPerformancePanel } from "@/components/forecast/preview-performance-panel";
import { UserAdministrationView } from "./user-administration-view";
import { CaptureCenterView } from "./capture-center-view";
import { selectedForecastHorizons, useCurrentForecastSelection } from "@/lib/forecast-e4-ui";

const modules = [
  ["inicio", "Inicio", Home], ["historico", "Histórico", History],
  ["motor", "Motores de Forecast", BrainCircuit], ["calidad", "Calidad de datos", ShieldCheck],
  ["periodos", "Periodos", CalendarRange], ["captura", "Centro de captura", ClipboardCheck],
  ["usuarios", "Usuarios", Users], ["auditoria", "Auditoría", FileClock],
] as const;
type ModuleId = typeof modules[number][0];
export type PreviewView = { key: string; result: PreviewJob | null; job: PreviewJob | null; error: string; loading: boolean };
type FilterState = { filters: Filters; setFilters: React.Dispatch<React.SetStateAction<Filters>>; repository: ForecastReadRepository; profile: Profile; seriesVisibility?: SeriesVisibility; setSeriesVisibility?: React.Dispatch<React.SetStateAction<SeriesVisibility>>; preview?: PreviewView | null; setPreview?: React.Dispatch<React.SetStateAction<PreviewView | null>> };
export const ForecastFiltersContext = createContext<FilterState | null>(null);
export function useForecastFilters() {
  const state = useContext(ForecastFiltersContext);
  if (!state) throw new Error("filter_provider_required");
  return state;
}
function useRead<T>(load: () => Promise<T>) {
  const [result, setResult] = useState<{ load: typeof load; data: T | null; error: boolean } | null>(null);
  useEffect(() => {
    let alive = true;
    void Promise.resolve().then(load).then(data => {
      if (alive) setResult({ load, data, error: false });
    }).catch(() => { if (alive) { logPortalEvent("read_error"); setResult({ load, data: null, error: true }); } });
    return () => { alive = false; };
  }, [load]);
  // Never render the previous filter/user's result during an async transition.
  return result?.load === load ? { loading: false, ...result } : { loading: true, data: null, error: false };
}
const number = (n: number | null) => n === null ? "Sin dato" : n.toLocaleString("es-MX", { maximumFractionDigits: 3 });
export function ReadState({ loading, error, empty }: { loading: boolean; error: boolean; empty?: boolean }) {
  if (loading) return <p role="status" className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-500">Consultando histórico certificado…</p>;
  if (error) return <p role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-5 text-sm text-rose-700">No fue posible consultar los datos.</p>;
  if (empty) return <p className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-500">No hay histórico certificado para este filtro.</p>;
  return null;
}
function Picker({ label, value, onChange, options, all = "Todas", disabled = false }: { label: string; value: string | null; onChange: (value: string | null) => void; options: { id: string; name: string }[]; all?: string; disabled?: boolean }) {
  return <div className="min-w-0 space-y-2"><Label>{label}</Label><Select value={value ?? "all"} onValueChange={v => onChange(v === "all" ? null : v)} disabled={disabled}><SelectTrigger aria-label={label} className="w-full min-w-0 bg-white"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">{all}</SelectItem>{options.map(o => <SelectItem value={o.id} key={o.id}>{o.name}</SelectItem>)}</SelectContent></Select></div>;
}

export default function ForecastTowellApp({ profile, repository, onLogout }: { profile: Profile; repository: ForecastReadRepository; onLogout: () => Promise<void> }) {
  const [active, setActive] = useState<ModuleId>("inicio");
  const [motorVisited, setMotorVisited] = useState(false);
  const [seriesVisibility, setSeriesVisibility] = useState<SeriesVisibility>({ ...defaultVisibility });
  const [filters, setFilters] = useState<Filters>({ ...emptyFilters });
  const [previewView, setPreview] = useState<PreviewView | null>(null);
  const previewKey = `${filters.chainId}/${filters.productId}`;
  useEffect(() => {
    let alive = true;
    const client = repository.previews;
    if (client) void client.latest(filters.chainId, filters.productId).then(job => {
      if (alive) setPreview(previous => previous?.key === previewKey && (previous.loading || previous.job) ? previous : { key: previewKey, result: ["READY_PREVIEW", "READY_PROVISIONAL"].includes(job?.status ?? "") ? job : null, job, error: "", loading: false });
    }).catch(error => {
      if (alive) setPreview(previous => previous?.key === previewKey && (previous.loading || previous.job) ? previous : { key: previewKey, result: null, job: null, error: error instanceof PreviewReadError ? error.code : "DATA_READ_FAILED", loading: false });
    });
    return () => { alive = false; };
  }, [repository, filters.chainId, filters.productId, previewKey]);
  const state = useMemo(() => ({ filters, setFilters, repository, profile, seriesVisibility, setSeriesVisibility, preview: previewView?.key === previewKey ? previewView : null, setPreview }), [filters, repository, profile, seriesVisibility, previewView, previewKey]);
  const chains = useRead(useCallback(() => repository.getVisibleChains(), [repository]));
  const subtitle = chains.data?.find(c => c.id === filters.chainId)?.name ?? (profile.global_role === "ADMIN" ? "Todas las cadenas" : "Todas las cadenas autorizadas");
  const title = modules.find(([id]) => id === active)?.[1] ?? "Inicio";
  const assistantContext: AssistantContext = {
    user: profile.id, role: profile.global_role === "ADMIN" ? "manager" : profile.global_role === "EDITOR" ? "editor" : "reader",
    globalRole: profile.global_role, screen: title, moduleId: active,
    chain: subtitle, chainId: filters.chainId, category: filters.categoryId, categoryId: filters.categoryId,
    product: filters.productId, productId: filters.productId, color: null, period: null,
    periodRange: filters.periodRange, search: filters.search,
    activeFilters: { chainId: filters.chainId, categoryId: filters.categoryId, productId: filters.productId, periodFrom: filters.periodRange[0], periodTo: filters.periodRange[1], search: filters.search },
  };
  return <ForecastFiltersContext.Provider value={state}><SidebarProvider>
    <Sidebar collapsible="icon" className="border-r border-slate-200">
      <SidebarHeader className="border-b border-slate-200 p-4"><div className="flex items-center gap-3"><div className="grid size-9 shrink-0 place-items-center rounded-xl bg-blue-700 text-sm font-black text-white">FT</div><div className="min-w-0 group-data-[collapsible=icon]:hidden"><p className="truncate text-sm font-bold">FORECAST Towell</p><p className="text-xs text-slate-500">Operación y control</p></div></div></SidebarHeader>
      <SidebarContent><SidebarGroup><SidebarGroupLabel>Módulos</SidebarGroupLabel><SidebarGroupContent><SidebarMenu>{modules.filter(([id]) => id !== "usuarios" || profile.global_role === "ADMIN").map(([id, label, Icon]) => <SidebarMenuItem key={id}><SidebarMenuButton isActive={active === id} tooltip={label} onClick={() => setActive(id)}><Icon/><span>{label}</span></SidebarMenuButton></SidebarMenuItem>)}</SidebarMenu></SidebarGroupContent></SidebarGroup></SidebarContent>
      <SidebarFooter className="border-t border-slate-200 p-3"><div className="rounded-xl bg-slate-900 p-3 text-white group-data-[collapsible=icon]:hidden"><p className="text-xs font-semibold text-slate-300">Scope seleccionado</p><p className="mt-1 break-words text-sm">{subtitle}</p><p className="mt-2 text-xs text-slate-300">Histórico certificado · Solo lectura</p></div></SidebarFooter>
    </Sidebar>
    <SidebarInset className="min-w-0 bg-[#f7f9fc]">
      <header className="sticky top-0 z-30 flex min-h-16 flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-white/95 px-4 py-2 backdrop-blur md:px-7"><div className="flex min-w-0 items-center gap-3"><SidebarTrigger aria-label="Abrir navegación"><PanelLeft/></SidebarTrigger><div className="h-6 w-px bg-slate-200"/><div className="min-w-0"><p className="truncate text-sm font-semibold text-slate-950">{active === "inicio" ? "FORECAST Towell" : title}</p><p className="truncate text-xs text-slate-500">{subtitle}</p></div></div><div className="flex items-center gap-3"><div><p className="max-w-40 truncate text-sm font-medium">{profile.full_name || "Usuario"}</p><p className="text-xs text-slate-500">{profile.global_role}</p></div><Button variant="outline" size="sm" onClick={() => { setFilters({ ...emptyFilters }); void onLogout(); }}><LogOut className="size-4"/><span className="hidden sm:inline">Cerrar sesión</span><span className="sr-only sm:hidden">Cerrar sesión</span></Button></div></header>
      <main className="mx-auto w-full max-w-[1480px] p-4 md:p-7"><GlobalFilters/>
        {active === "inicio" && <Dashboard/>}
        {active === "historico" && <HistoryView/>}
        {(active === "motor" || motorVisited) && <div hidden={active !== "motor"}><ForecastEnginesView scope={subtitle} onVisit={() => setMotorVisited(true)}/></div>}
        {active === "calidad" && <QualityView/>}
        {active === "periodos" && <PeriodsView/>}
        {active === "usuarios" && profile.global_role === "ADMIN" && <UsersView/>}
        {active === "captura" && <CaptureCenterView key={`${filters.chainId}/${filters.productId}`}/>}
        {active === "auditoria" && <Unavailable title="Auditoría" copy="La consulta de auditoría operativa se habilitará en una fase posterior. No se muestran eventos simulados."/>}
      </main>
    </SidebarInset>
    <ForecastAssistantErrorBoundary><ForecastAssistant authorized={profile.status === "ACTIVE"} uiEnabled apiEnabled={false} voiceEnabled={false} mode="local" context={assistantContext}/></ForecastAssistantErrorBoundary>
  </SidebarProvider></ForecastFiltersContext.Provider>;
}

export function GlobalFilters() {
  const { repository, filters, setFilters, profile } = useForecastFilters();
  const [showEmpty, setShowEmpty] = useState(false);
  const chains = useRead(useCallback(() => repository.getVisibleChains(), [repository]));
  const categories = useRead(useCallback(() => repository.getCategories(filters.chainId), [repository, filters.chainId]));
  const products = useRead(useCallback(() => repository.getProducts(filters.chainId, filters.categoryId, filters.search), [repository, filters.chainId, filters.categoryId, filters.search]));
  const periods = useRead(useCallback(() => repository.getPeriods(filters.chainId), [repository, filters.chainId]));
  const chainMap = new Map(chains.data?.map(c => [c.id, c.name]) ?? []);
  const options = (chains.data ?? []).filter(c => c.has_history || showEmpty).map(c => ({ id: c.id, name: `${c.parentId && chainMap.has(c.parentId) ? chainMap.get(c.parentId) + " → " : ""}${c.name}${c.has_history ? "" : " · Sin histórico"}` }));
  return <section aria-label="Filtros globales" className="mb-6 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Picker label="Cadena / unidad comercial" value={filters.chainId} onChange={id => setFilters(f => resetForChain(f, id))} options={options} disabled={chains.loading || chains.error} all={profile.global_role === "ADMIN" ? "Todas las cadenas" : "Todas las autorizadas"}/>
      <Picker label="Categoría" value={filters.categoryId} onChange={id => setFilters(f => ({ ...f, categoryId: id, productId: null }))} options={(categories.data ?? []).map(c => ({ id: c.id, name: !filters.chainId && chainMap.has(c.chain_id) ? `${c.name} · ${chainMap.get(c.chain_id)}` : c.name }))} disabled={categories.loading || categories.error}/>
      <Picker label="Producto" value={filters.productId} onChange={id => setFilters(f => {
        if (!id) return { ...f, productId: null };
        const selected = products.data?.find(p => p.id === id);
        // Product identity includes its chain. A product picked from "Todas"
        // must never be sent to Railway without that exact scope.
        if (selected && f.chainId !== selected.chain_id) return { ...resetForChain(f, selected.chain_id), productId: id };
        return { ...f, productId: id };
      })} options={(products.data ?? []).slice(0, 100).map(p => ({ id: p.id, name: `${p.description} · ${p.product_code}${p.variant_code ? " · " + p.variant_code : ""}` }))} disabled={products.loading || products.error} all="Todos los productos"/>
      <div className="space-y-2"><Label htmlFor="product-search">Buscar producto, ITEM, UPC o variante</Label><Input id="product-search" type="search" maxLength={100} value={filters.search} onChange={e => setFilters(f => ({ ...f, search: e.target.value, productId: null }))} placeholder="Buscar en catálogo autorizado"/></div>
    </div>
    <div className="mt-4 grid gap-4 sm:grid-cols-3">
      <Picker label="Periodo desde" value={filters.periodRange[0]} onChange={id => setFilters(f => ({ ...f, periodRange: [id, f.periodRange[1] && id && f.periodRange[1] < id ? id : f.periodRange[1]] }))} options={(periods.data ?? []).map(p => ({ id: p, name: p }))} disabled={periods.loading || periods.error} all="Desde el inicio"/>
      <Picker label="Periodo hasta" value={filters.periodRange[1]} onChange={id => setFilters(f => ({ ...f, periodRange: [f.periodRange[0] && id && f.periodRange[0] > id ? id : f.periodRange[0], id] }))} options={(periods.data ?? []).map(p => ({ id: p, name: p }))} disabled={periods.loading || periods.error} all="Hasta el último"/>
      <div className="flex items-end gap-4 pb-1"><label className="flex items-center gap-2 text-sm text-slate-600"><input type="checkbox" checked={showEmpty} onChange={e => setShowEmpty(e.target.checked)}/>Mostrar scopes sin histórico</label><Button variant="ghost" size="sm" onClick={() => setFilters({ ...emptyFilters })}>Limpiar</Button></div>
    </div>
    {(products.data?.length ?? 0) > 100 && <p className="mt-3 text-sm text-slate-500">{number(products.data!.length)} productos coinciden. Usa la búsqueda para encontrar un producto específico.</p>}
    {[[chains.error, "las cadenas"], [categories.error, "las categorías"], [products.error, "los productos"], [periods.error, "los periodos"]].map(([failed, control]) => failed && <p key={String(control)} role="alert" className="mt-3 text-sm text-rose-700">No fue posible cargar {control}.{control === "los periodos" && repository.getReadDiagnostic?.("getPeriods")?.code ? ` Código ${repository.getReadDiagnostic?.("getPeriods")?.code}.` : ""}</p>)}
  </section>;
}
function Intro({ title, copy }: { title: string; copy: string }) { return <div className="mb-5"><h1 className="text-2xl font-semibold tracking-tight text-slate-950 sm:text-3xl">{title}</h1><p className="mt-2 text-sm leading-6 text-slate-500">{copy}</p></div>; }
export function Dashboard() {
  const { repository, filters, preview, seriesVisibility, setSeriesVisibility } = useForecastFilters();
  const currentSelection = useCurrentForecastSelection(repository.previews, filters.chainId, filters.productId);
  const summary = useRead(useCallback(() => repository.getHistoricalSummary(filters), [repository, filters]));
  const scope = filters.chainId ? preview?.result?.scopes.find(s => s.chain_id === filters.chainId) ?? null : null;
  const validScope = currentRetrospectivePreview(preview?.result ?? null, scope) ? scope : null;
  const chartScope = validScope ?? (scope?.provisional_cold_start ? scope : null);
  const cutoff = String(chartScope?.issue_period ?? "");
  const historical = useRead(useCallback(() => filters.chainId ? repository.getForecastHistory?.(filters) ?? Promise.resolve([]) : Promise.resolve([]), [repository, filters]));
  const customer = useRead(useCallback(() => filters.chainId && cutoff ? repository.getCustomerForecast?.(filters, cutoff) ?? Promise.resolve([]) : Promise.resolve([]), [repository, filters, cutoff]));
  const gates = useRead(useCallback(() => filters.chainId && validScope && repository.previews?.qualityGates
    ? repository.previews.qualityGates(filters.chainId) : Promise.resolve(null),
    [repository, filters.chainId, validScope]));
  const quality = gates.data?.dataset_hash === validScope?.dataset_hash ? gates.data : null;
  const currentHorizons = selectedForecastHorizons(currentSelection);
  const chartCutoff = currentHorizons.length === 12 ? currentSelection?.issue_period.slice(0,7) : cutoff;
  const points = traderPoints(historical.data ?? [], customer.data ?? [], currentHorizons.length ? currentHorizons : scopeHorizons(validScope, filters), chartCutoff, filters, scopeProvisionalHorizons(chartScope, filters));
  return <div><Intro title="Dashboard ejecutivo" copy="Histórico publicado según tu acceso y los filtros seleccionados."/><ReadState loading={summary.loading} error={summary.error} empty={summary.data?.observationCount === 0}/>{summary.data && <>
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{[["Scopes con histórico", number(summary.data.scopeCount)], ["Productos visibles", number(summary.data.productCount)], ["Observaciones", number(summary.data.observationCount)], ["Último periodo disponible", summary.data.latestPeriod ?? "Sin dato"]].map(([label, value]) => <Card key={label} className="border-slate-200 shadow-sm"><CardContent className="p-5"><p className="text-sm text-slate-500">{label}</p><p className="mt-4 text-3xl font-semibold tracking-tight text-slate-950">{value}</p></CardContent></Card>)}</div>
    <div className="mt-5 space-y-5">{filters.chainId ? <>
      {currentSelection && <p className="rounded-xl border border-blue-200 bg-blue-50 p-4 text-sm text-blue-950"><strong>Forecast Towell vigente:</strong> {currentSelection.calculation_code} · {currentSelection.selected_candidate} · H1–H12 seleccionados. No es Champion publicado.</p>}
      <PreviewPerformancePanel job={preview?.result ?? null} scope={scope} productId={filters.productId}/>
      <Card className="border-slate-200 shadow-sm"><CardContent className="p-5"><h2 className="font-semibold">Calidad y nivel de servicio</h2>{quality ? <div className="mt-3 grid gap-3 text-sm sm:grid-cols-2 xl:grid-cols-4"><div><p className="text-slate-500">Calidad de datos</p><p className="font-medium">{quality.data_quality.status}</p></div><div><p className="text-slate-500">Calidad del forecast</p><p className="font-medium">{quality.forecast_quality.status}</p></div><div><p className="text-slate-500">Fill Rate observado</p><p className="font-medium">{quality.service_level.observed_fill_rate === null ? "—" : `${quality.service_level.observed_fill_rate.toFixed(2)}%`}</p><p className="text-xs text-slate-500">Objetivo {quality.service_level.target_fill_rate}% · Brecha {quality.service_level.gap_pp === null ? "—" : `${quality.service_level.gap_pp.toFixed(2)} pp`}</p></div><div><p className="text-slate-500">Publicación</p><p className="font-medium">{quality.publication}</p><p className="text-xs text-slate-500">Simulación logística pendiente</p></div></div> : <p className="mt-2 text-sm text-slate-500">{gates.loading ? "Evaluando calidad…" : gates.error ? "Calidad no disponible para este preview." : "Sin evaluación de calidad para este preview."}</p>}</CardContent></Card>
      {historical.error && <p role="alert" className="text-sm text-rose-700">No fue posible consultar la venta histórica para la gráfica.</p>}
      {customer.error && <p className="text-sm text-slate-500">Fcst Cliente no disponible; no se sustituyen datos ausentes.</p>}
      {historical.loading ? <p role="status" className="rounded-xl border bg-white p-5 text-sm text-slate-500">Preparando gráfica histórica…</p> : points.length ? <ForecastTraderChart points={points} cutoff={chartCutoff} visible={seriesVisibility ?? defaultVisibility} onChange={key => setSeriesVisibility?.(v => ({ ...v, [key]: !v[key] }))} operationalSelection={currentHorizons.length === 12}/> : <p className="rounded-xl border bg-white p-5 text-sm text-slate-500">No hay meses publicados para graficar en este filtro.</p>}
    </> : <DashboardAllScopesChart scopes={summary.data.byScope} />}</div>
    <Card className="mt-5 border-slate-200 shadow-sm"><CardContent className="p-5"><h2 className="font-semibold">Cobertura por scope</h2><p className="mt-2 text-sm text-slate-500">Los scopes padre e hijo se presentan por separado. El número de observaciones es un conteo de registros, no una suma de cantidades entre niveles.</p><div className="mt-4"><DataTable headers={["Cadena / unidad comercial", "Observaciones"]} rows={summary.data.byScope.map(s => [s.chain.name, number(s.observations)])}/></div></CardContent></Card>
  </>}</div>;
}
function DashboardAllScopesChart({ scopes }: { scopes: import("@/lib/supabase/types").Summary["byScope"] }) {
  const { repository, filters, seriesVisibility, setSeriesVisibility } = useForecastFilters();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = scopes.find(row => row.chain.id === selectedId) ?? scopes[0];
  const chainId = selected?.chain.id ?? null;
  const chartFilters = useMemo<Filters>(() => ({ ...filters, chainId, productId: null }), [filters, chainId]);
  const history = useRead(useCallback(() => chainId ? repository.getForecastHistory?.(chartFilters) ?? Promise.resolve([]) : Promise.resolve([]), [repository, chainId, chartFilters]));
  const preview = useRead(useCallback(() => chainId ? repository.previews?.latest(chainId, null) ?? Promise.resolve(null) : Promise.resolve(null), [repository, chainId]));
  const scope = preview.data?.status === "READY_PREVIEW" ? preview.data.scopes.find(row => row.chain_id === chainId) ?? null : null;
  const validScope = currentRetrospectivePreview(preview.data ?? null, scope) ? scope : null;
  const cutoff = String(validScope?.issue_period ?? history.data?.at(-1)?.period ?? "");
  const customer = useRead(useCallback(() => chainId && cutoff ? repository.getCustomerForecast?.(chartFilters, cutoff) ?? Promise.resolve([]) : Promise.resolve([]), [repository, chainId, chartFilters, cutoff]));
  if (!selected) return <p className="rounded-xl border bg-white p-5 text-sm text-slate-500">No hay un scope con histórico visible para graficar.</p>;
  const points = traderPoints(history.data ?? [], customer.data ?? [], scopeHorizons(validScope, chartFilters), cutoff, chartFilters);
  if (!repository.getForecastHistory) return <p className="rounded-xl border bg-white p-5 text-sm text-slate-500">La lectura histórica para la gráfica no está disponible.</p>;
  return <div className="space-y-3">
    <div className="rounded-xl border border-slate-200 bg-white p-4"><Picker label="Cadena mostrada en la gráfica" value={chainId} onChange={setSelectedId} options={scopes.map(row => ({ id: row.chain.id, name: row.chain.name }))} all={selected.chain.name}/><p className="mt-2 text-xs text-slate-500">Vista de una cadena a la vez; no se suman scopes padre e hijo. Cambiar la gráfica no ejecuta modelos.</p></div>
    {history.error && <p role="alert" className="text-sm text-rose-700">No fue posible consultar el histórico de esta gráfica.</p>}
    {preview.error && <p className="text-xs text-slate-500">La vista previa operacional no está disponible; se conserva el histórico.</p>}
    {customer.error && <p className="text-xs text-slate-500">Fcst Cliente no disponible; no se sustituyen datos ausentes.</p>}
    {history.loading ? <p role="status" className="rounded-xl border bg-white p-5 text-sm text-slate-500">Preparando gráfica histórica…</p> : points.length ? <ForecastTraderChart points={points} cutoff={cutoff} visible={seriesVisibility ?? defaultVisibility} onChange={key => setSeriesVisibility?.(value => ({ ...value, [key]: !value[key] }))}/> : <p className="rounded-xl border bg-white p-5 text-sm text-slate-500">No hay meses publicados para graficar en esta cadena.</p>}
  </div>;
}
export function HistoryView() {
  const { repository, filters } = useForecastFilters();
  // Filter changes remount page state; cursors from another scope cannot survive.
  return <HistoricalPages key={JSON.stringify(filters)} repository={repository} filters={filters}/>;
}
function HistoricalPages({ repository, filters }: { repository: ForecastReadRepository; filters: Filters }) {
  const { preview } = useForecastFilters();
  const scope = filters.chainId ? preview?.result?.scopes.find(s => s.chain_id === filters.chainId) ?? null : null;
  const [size, setSize] = useState<50 | 100 | 250>(50);
  const [cursors, setCursors] = useState<(Cursor | null)[]>([null]);
  const cursor = cursors.at(-1) ?? null;
  const page = useRead(useCallback(() => repository.getHistoricalObservations(filters, { size, cursor }), [repository, filters, size, cursor]));
  return <div><Intro title="Histórico operativo" copy="Pedido, venta y entrega por scope, producto y periodo. Vacío significa sin dato; cero es una cantidad observada."/>
    {filters.chainId ? <div className="mb-5"><PreviewPerformancePanel job={preview?.result ?? null} scope={scope} productId={filters.productId} historical/></div> : <p className="mb-5 rounded-xl border bg-white p-4 text-sm text-slate-500">Selecciona una cadena para consultar WAPE y Bias sin mezclar scopes. Evaluación retrospectiva rolling-origin. No constituye certificación point-in-time.</p>}
    <ReadState loading={page.loading} error={page.error} empty={page.data?.rows.length === 0}/>{page.data && page.data.rows.length > 0 && <DataTable headers={["Periodo", "Cadena", "Producto", "UPC", "ITEM", "Pedido", "Venta", "Entrega"]} rows={page.data.rows.map(r => [r.period.slice(0, 7), r.chain, r.product.description, [...new Set(r.product.identifiers.map(i => i[1]).filter(Boolean))].join(" / ") || "Sin dato", r.product.product_code, number(r.ORDER), number(r.SALES), number(r.DELIVERY)])}/>}
    <div className="mt-4 flex flex-wrap items-end justify-between gap-3"><Picker label="Filas por página" value={String(size)} onChange={v => { setSize(Number(v) as 50 | 100 | 250); setCursors([null]); }} options={[50, 100, 250].map(n => ({ id: String(n), name: String(n) }))} all="50"/><div className="flex items-center gap-3"><Button variant="outline" disabled={cursors.length === 1 || page.loading} onClick={() => setCursors(c => c.slice(0, -1))}>Anterior</Button><span className="text-sm text-slate-500">Página {cursors.length}</span><Button variant="outline" disabled={!page.data?.next || page.loading} onClick={() => setCursors(c => [...c, page.data!.next])}>Siguiente</Button></div></div>
  </div>;
}
function QualityView() { return <Unavailable title="Calidad de datos" copy="Esta vista consulta únicamente observaciones publicadas. Los registros pendientes de reconciliación no forman parte del histórico visible. No se muestran alertas demo ni métricas de modelos sin corrida publicada."/>; }
function PeriodsView() {
  const { repository, filters } = useForecastFilters();
  const periods = useRead(useCallback(() => repository.getPeriods(filters.chainId), [repository, filters.chainId]));
  return <div><Intro title="Periodos disponibles" copy="Periodos derivados de las observaciones visibles. La operación de cierre no está habilitada."/><ReadState loading={periods.loading} error={periods.error} empty={periods.data?.length === 0}/>{periods.data && <DataTable headers={["Periodo", "Disponibilidad"]} rows={periods.data.map(p => [p, "Histórico publicado"])}/>}</div>;
}
export function UsersView() {
  const { repository, profile } = useForecastFilters();
  if (profile.global_role !== "ADMIN") return null;
  return <UserAdministrationView repository={repository} profile={profile}/>;
}
function Unavailable({ title, copy }: { title: string; copy: string }) { return <div><Intro title={title} copy={copy}/><Card className="border-slate-200 shadow-sm"><CardContent className="p-6 text-sm text-slate-500">Módulo no habilitado para operaciones en esta fase.</CardContent></Card></div>; }
function DataTable({ headers, rows }: { headers: string[]; rows: string[][] }) { return <div className="overflow-hidden rounded-xl border border-slate-200 bg-white"><div className="overflow-x-auto"><Table><TableHeader><TableRow className="bg-slate-50">{headers.map(h => <TableHead className="whitespace-nowrap font-semibold text-slate-700" key={h}>{h}</TableHead>)}</TableRow></TableHeader><TableBody>{rows.map((row, i) => <TableRow key={i}>{row.map((cell, j) => <TableCell key={j} className={j < 3 ? "min-w-24 text-slate-700" : "whitespace-nowrap text-slate-600"}>{cell}</TableCell>)}</TableRow>)}</TableBody></Table></div></div>; }
