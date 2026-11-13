"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { BrainCircuit, CalendarRange, ClipboardCheck, Database, History, Home, LogOut, PanelLeft, ShieldCheck, Users, RotateCcw, SlidersHorizontal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarGroupLabel, SidebarHeader, SidebarInset, SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { logPortalEvent } from "@/lib/supabase/client";
import { resetForChain } from "@/lib/forecast-data";
import { emptyFilters, type Chain, type Filters, type ForecastReadRepository, type OperationalReadContext, type Profile } from "@/lib/supabase/types";
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
import { ForecastPeriodsView } from "./forecast-periods-view";
import { CommercialHistoryView } from "./commercial-history-view";
import { DatabaseView } from "./database-view";
import { OperationalQualityView } from "./operational-quality-view";
import { attachCommercial, changeCommercial, commercialOptions, hasCommercialFilters, matchesCommercial, previewScopeKey, selectedScopeIds, type CommercialFilter } from "@/lib/commercial-filters";
import { ModelMultiselect } from "@/components/forecast/model-multiselect";
import { PeriodsComparisonChart } from "@/components/forecast/periods-comparison-chart";
import { periodsDashboard } from "@/lib/periods-dashboard";
import { emptyReportChoice, leafScopeIds, matchesOfficialSource } from "@/lib/forecast-reporting";

const modules = [
  ["inicio", "Inicio", Home], ["historico", "Histórico", History],
  ["motor", "Motores de Forecast", BrainCircuit], ["calidad", "Calidad de datos", ShieldCheck],
  ["periodos", "Periodos", CalendarRange], ["captura", "Centro de captura", ClipboardCheck],
  ["base-datos", "Base de Datos", Database], ["usuarios", "Usuarios", Users],
] as const;
type ModuleId = typeof modules[number][0];
export type PreviewView = { key: string; result: PreviewJob | null; job: PreviewJob | null; error: string; loading: boolean };
type FilterState = { filters: Filters; setFilters: React.Dispatch<React.SetStateAction<Filters>>; repository: ForecastReadRepository; profile: Profile; pinOperational?: () => Promise<OperationalReadContext | undefined>; seriesVisibility?: SeriesVisibility; setSeriesVisibility?: React.Dispatch<React.SetStateAction<SeriesVisibility>>; preview?: PreviewView | null; setPreview?: React.Dispatch<React.SetStateAction<PreviewView | null>> };
export const ForecastFiltersContext = createContext<FilterState | null>(null);
export function useForecastFilters() {
  const state = useContext(ForecastFiltersContext);
  if (!state) throw new Error("filter_provider_required");
  return state;
}
export function useOperationPin() {
  const { repository, pinOperational } = useForecastFilters();
  return useCallback(() => pinOperational?.() ?? repository.openOperationalRead?.() ?? Promise.resolve(undefined), [repository, pinOperational]);
}
export function useRead<T>(load: () => Promise<T>) {
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
  return <div className="min-w-0 space-y-2"><Label>{label}</Label><Select value={value ?? "all"} onValueChange={v => onChange(v === "all" ? null : v)} disabled={disabled}><SelectTrigger aria-label={label} className="w-full min-w-0 bg-white [&>span]:truncate"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="all">{all}</SelectItem>{options.map(o => <SelectItem value={o.id} key={o.id}>{o.name}</SelectItem>)}</SelectContent></Select></div>;
}

// Parent selection explicitly resolves its authorized source scopes. It never
// replaces a scope UUID with a guessed identity or counts parent and child twice.
export function commercialScopeNavigation(chains: Chain[], chainId: string | null, showEmpty = false) {
  const byId = new Map(chains.map(chain => [chain.id, chain]));
  const selected = chainId ? byId.get(chainId) : undefined;
  let invalid = false;
  const ancestry = (chain: Chain) => {
    const result = [chain.id], seen = new Set(result);
    let current = chain;
    while (current.parentId && byId.has(current.parentId)) {
      if (seen.has(current.parentId)) { invalid = true; break; }
      current = byId.get(current.parentId)!; seen.add(current.id); result.push(current.id);
    }
    return result;
  };
  const parentId = selected ? ancestry(selected).at(-1)! : null;
  const visible = chains.filter(chain => chain.has_history || showEmpty);
  const parents = [...new Set(visible.map(chain => ancestry(chain).at(-1)!))];
  const members = parentId ? visible.filter(chain => ancestry(chain).includes(parentId)) : [];
  let leaves = new Set<string>();
  try { leaves = leafScopeIds(chains, new Set(members.map(chain => chain.id))); } catch { invalid = true; }
  const formats = members.filter(chain => leaves.has(chain.id));
  return { parentId, pending: false, invalid, chains: parents.map(id => byId.get(id)!), formats, scopeIds: formats.map(chain => chain.id) };
}
async function scopeUnion<T extends { id: string }>(ids: string[] | null, read: (id: string | null) => Promise<T[]>) {
  const pages = ids === null ? [await read(null)] : await Promise.all(ids.map(read));
  return [...new Map(pages.flat().map(row => [row.id, row])).values()];
}

export default function ForecastTowellApp({ profile, repository, onLogout }: { profile: Profile; repository: ForecastReadRepository; onLogout: () => Promise<void> }) {
  const [active, setActive] = useState<ModuleId>("inicio");
  const [motorVisited, setMotorVisited] = useState(false);
  const [seriesVisibility, setSeriesVisibility] = useState<SeriesVisibility>({ ...defaultVisibility });
  const [filters, setFilters] = useState<Filters>(() => ({ ...emptyFilters, ...(repository.getCommercialCatalog && repository.openOperationalRead ? { purchaseType: "RS" as const } : {}) }));
  // One source fingerprint per interaction. A source update invalidates its
  // pending reads, so paging and exports cannot combine different inputs.
  const pinOperational = useMemo(() => {
    let pin: Promise<OperationalReadContext | undefined> | undefined;
    return () => pin ??= repository.openOperationalRead?.() ?? Promise.resolve(undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- Module and filters define the lifetime of the read.
  }, [repository, active, filters]);
  const chains = useRead(useCallback(async () => repository.getVisibleChains(await pinOperational()), [repository, pinOperational]));
  const selectedIds = selectedScopeIds(filters);
  const multipleFormats = Boolean(selectedIds && selectedIds.length > 1);
  const formatPending = Boolean(repository.getCommercialCatalog && repository.openOperationalRead &&
    (selectedIds?.length === 0 || filters.chainId &&
      (chains.loading || chains.error || !chains.data?.some(c => c.id === filters.chainId) || commercialScopeNavigation(chains.data ?? [], filters.chainId).invalid ||
        selectedIds?.some(id => !chains.data?.some(c => c.id === id)) ||
        filters.scopeChainIds === undefined && chains.data?.some(c => c.parentId === filters.chainId))));
  const isCE = filters.purchaseType === "CE";
  const [previewView, setPreview] = useState<PreviewView | null>(null);
  const previewRead = useRef<{ repository: ForecastReadRepository; key: string; source?: OperationalReadContext; task: Promise<PreviewJob | null> } | null>(null);
  const previewKey = previewScopeKey(filters);
  const previewChainId = selectedIds?.length === 1 ? selectedIds[0] : filters.chainId;
  useEffect(() => {
    let alive = true;
    const client = repository.previews;
    if (client && !formatPending && !isCE && !multipleFormats) void pinOperational().then(async source => {
      const cached = previewRead.current;
      const sameSource = source ? Boolean(cached?.source && matchesOfficialSource(cached.source, source)) : !cached?.source;
      // Revalidate provenance on navigation, but reuse the same endpoint read
      // (including an in-flight request) while its scope and source are fixed.
      const read = cached?.repository === repository && cached.key === previewKey && sameSource ? cached : {
        repository, key: previewKey, source, task: client.latest(previewChainId, filters.productId, source),
      };
      previewRead.current = read;
      return { source, job: await read.task };
    }).then(({ source, job }) => {
      if (alive) setPreview(previous => {
        const compatible = !source || Boolean(previous?.result?.scopes.length && previous.result.scopes.every(scope => matchesOfficialSource(scope.source_context, source)));
        return compatible && previous?.key === previewKey && (previous.loading || previous.job) ? previous : { key: previewKey, result: ["READY_PREVIEW", "READY_PROVISIONAL"].includes(job?.status ?? "") ? job : null, job, error: "", loading: false };
      });
    }).catch(error => {
      if (alive) {
        previewRead.current = null;
        setPreview({ key: previewKey, result: null, job: null, error: error instanceof PreviewReadError ? error.code : "DATA_READ_FAILED", loading: false });
      }
    });
    return () => { alive = false; };
  }, [repository, previewChainId, filters.productId, previewKey, pinOperational, formatPending, isCE, multipleFormats]);
  const state = useMemo(() => ({ filters, setFilters, repository, profile, pinOperational, seriesVisibility, setSeriesVisibility, preview: previewView?.key === previewKey ? previewView : null, setPreview }), [filters, repository, profile, pinOperational, seriesVisibility, previewView, previewKey]);
  const subtitle = chains.data?.find(c => c.id === filters.chainId)?.name ?? (profile.global_role === "ADMIN" ? "Todas las cadenas" : "Todas las cadenas autorizadas");
  const title = modules.find(([id]) => id === active)?.[1] ?? "Inicio";
  const assistantContext: AssistantContext = {
    user: profile.id, role: profile.global_role === "ADMIN" ? "manager" : profile.global_role === "EDITOR" ? "editor" : "reader",
    globalRole: profile.global_role, screen: title, moduleId: active,
    chain: subtitle, chainId: filters.chainId, category: filters.categoryId, categoryId: filters.categoryId,
    product: filters.productId, productId: filters.productId, color: null, period: null,
    periodRange: filters.periodRange, commercialModels: filters.commercialModels, search: filters.search,
    activeFilters: { chainId: filters.chainId, categoryId: filters.categoryId, productId: filters.productId, purchaseType: filters.purchaseType ?? null, scopeChainIds: filters.scopeChainIds?.join(",") ?? null, periodFrom: filters.periodRange[0], periodTo: filters.periodRange[1], search: filters.search },
  };
  return <ForecastFiltersContext.Provider value={state}><SidebarProvider>
    <Sidebar collapsible="icon" className="border-r border-slate-200">
      <SidebarHeader className="border-b border-slate-200 p-4"><div className="flex items-center gap-3"><div className="grid size-9 shrink-0 place-items-center rounded-xl bg-blue-700 text-sm font-black text-white">FT</div><div className="min-w-0 group-data-[collapsible=icon]:hidden"><p className="truncate text-sm font-bold">FORECAST Towell</p><p className="text-xs text-slate-500">Operación y control</p></div></div></SidebarHeader>
      <SidebarContent><SidebarGroup><SidebarGroupLabel>Módulos</SidebarGroupLabel><SidebarGroupContent><SidebarMenu>{modules.filter(([id]) => id !== "usuarios" || profile.global_role === "ADMIN").map(([id, label, Icon]) => <SidebarMenuItem key={id}><SidebarMenuButton isActive={active === id} tooltip={label} onClick={() => setActive(id)}><Icon/><span>{label}</span></SidebarMenuButton></SidebarMenuItem>)}</SidebarMenu></SidebarGroupContent></SidebarGroup></SidebarContent>
      <SidebarFooter className="border-t border-slate-200 p-3"><div className="rounded-xl bg-slate-900 p-3 text-white group-data-[collapsible=icon]:hidden"><p className="text-xs font-semibold text-slate-300">Scope seleccionado</p><p className="mt-1 break-words text-sm">{subtitle}</p><p className="mt-2 text-xs text-slate-300">Histórico certificado · Solo lectura</p></div></SidebarFooter>
    </Sidebar>
    <SidebarInset className="min-w-0 bg-[#f7f9fc]">
      <header className="sticky top-0 z-30 flex min-h-16 flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-white/95 px-4 py-2 backdrop-blur md:px-7"><div className="flex min-w-0 items-center gap-3"><SidebarTrigger aria-label="Abrir navegación"><PanelLeft/></SidebarTrigger><div className="h-6 w-px bg-slate-200"/><div className="min-w-0"><p className="truncate text-sm font-semibold text-slate-950">{active === "inicio" ? "FORECAST Towell" : title}</p><p className="truncate text-xs text-slate-500">{subtitle}</p></div></div><div className="flex items-center gap-3"><div><p className="max-w-40 truncate text-sm font-medium">{profile.full_name || "Usuario"}</p><p className="text-xs text-slate-500">{profile.global_role}</p></div><Button variant="outline" size="sm" onClick={() => { setFilters({ ...emptyFilters }); void onLogout(); }}><LogOut className="size-4"/><span className="hidden sm:inline">Cerrar sesión</span><span className="sr-only sm:hidden">Cerrar sesión</span></Button></div></header>
      <main className="mx-auto w-full max-w-[1480px] p-4 md:p-7"><GlobalFilters compact={active === "periodos"}/>
        {isCE ? <p role="status" className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-600">Sin datos de Compra Especial (CE) incorporados en la fuente oficial activa.</p> : formatPending ? <p role="status" className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-600">Consultando el alcance comercial autorizado de la cadena.</p> : <>
        {active === "inicio" && <Dashboard/>}
        {active === "historico" && <HistoryView/>}
        {(active === "motor" || motorVisited) && <div hidden={active !== "motor"}><ForecastEnginesView scope={subtitle} onVisit={() => setMotorVisited(true)}/></div>}
        {active === "calidad" && <QualityView/>}
        {active === "periodos" && <ForecastPeriodsView/>}
        {active === "usuarios" && profile.global_role === "ADMIN" && <UsersView/>}
        {active === "captura" && <CaptureCenterView key={`${filters.chainId}/${filters.productId}`}/>}
        {active === "base-datos" && <DatabaseView/>}
        </>}
      </main>
    </SidebarInset>
    <ForecastAssistantErrorBoundary><ForecastAssistant authorized={profile.status === "ACTIVE" && !formatPending && !isCE && !multipleFormats} uiEnabled apiEnabled={false} voiceEnabled={false} mode="local" context={assistantContext} rsAssistant={formatPending || isCE || multipleFormats ? undefined : repository.previews?.rsAssistant}/></ForecastAssistantErrorBoundary>
  </SidebarProvider></ForecastFiltersContext.Provider>;
}

export function GlobalFilters({ compact = false }: { compact?: boolean }) {
  const { repository, filters, setFilters, profile } = useForecastFilters();
  const pin = useOperationPin();
  const [showEmpty, setShowEmpty] = useState(false);
  const [opened, setOpened] = useState(false);
  const chains = useRead(useCallback(async () => repository.getVisibleChains(await pin()), [repository, pin]));
  const commercialNavigation = Boolean(repository.getCommercialCatalog && repository.openOperationalRead);
  const navigation = useMemo(() => commercialScopeNavigation(chains.data ?? [], filters.chainId, showEmpty), [chains.data, filters.chainId, showEmpty]);
  const chainId = filters.chainId, scopeChainIds = filters.scopeChainIds, purchaseType = filters.purchaseType ?? "RS";
  const scopeIds = useMemo(() => selectedScopeIds({ ...emptyFilters, chainId, scopeChainIds }), [chainId, scopeChainIds]);
  const formatPending = commercialNavigation && Boolean(scopeIds?.length === 0 || filters.chainId &&
    (chains.loading || chains.error || !chains.data?.some(c => c.id === filters.chainId) || navigation.invalid ||
      scopeIds?.some(id => !chains.data?.some(c => c.id === id)) || filters.scopeChainIds === undefined && chains.data?.some(c => c.parentId === filters.chainId)));
  useEffect(() => {
    if (!commercialNavigation || !chains.data || !filters.chainId || filters.scopeChainIds !== undefined ||
      !chains.data.some(c => c.parentId === filters.chainId) || navigation.invalid) return;
    let alive = true;
    // Interpret an existing parent selection as the explicitly selected union;
    // no mandatory format step and no implicit unrestricted/null fallback.
    queueMicrotask(() => { if (alive) setFilters(previous => previous.chainId === filters.chainId && previous.scopeChainIds === undefined
      ? { ...previous, scopeChainIds: navigation.scopeIds } : previous); });
    return () => { alive = false; };
  }, [commercialNavigation, chains.data, filters.chainId, filters.scopeChainIds, navigation, setFilters]);
  const categories = useRead(useCallback(() => purchaseType === "CE" || formatPending ? Promise.resolve([]) : scopeUnion(scopeIds, id => repository.getCategories(id)), [repository, scopeIds, purchaseType, formatPending]));
  // Product text search narrows products, never the model catalogue of the scope.
  const products = useRead(useCallback(() => purchaseType === "CE" || formatPending ? Promise.resolve([]) : scopeUnion(scopeIds, id => repository.getProducts(id, filters.categoryId, "")), [repository, scopeIds, filters.categoryId, purchaseType, formatPending]));
  const searchedProducts = useRead(useCallback(() => purchaseType !== "CE" && !formatPending && filters.search ? scopeUnion(scopeIds, id => repository.getProducts(id, filters.categoryId, filters.search)) : Promise.resolve(null), [repository, scopeIds, filters.categoryId, filters.search, purchaseType, formatPending]));
  const commercial = useRead(useCallback(async () => purchaseType === "CE" ? [] : repository.getCommercialCatalog?.(await pin()) ?? null, [repository, pin, purchaseType]));
  const catalog = useMemo(() => {
    try { return commercial.data && products.data ? attachCommercial(products.data, commercial.data) : products.data ?? []; }
    catch { return []; }
  }, [products.data, commercial.data]);
  const commercialScope = commercialNavigation ? { ...filters, purchaseType } : filters;
  const searchIds = filters.search ? new Set(searchedProducts.data?.map(p => p.id) ?? []) : null;
  const matching = catalog.filter(p => matchesCommercial(p, commercialScope) && (!searchIds || searchIds.has(p.id)));
  const selectedProducts = matching.filter(p => !filters.productId || p.id === filters.productId);
  const periods = useRead(useCallback(async () => {
    if (formatPending || purchaseType === "CE") return [];
    const context = await pin();
    const pages = scopeIds === null ? [await (repository.getReportPeriods ? repository.getReportPeriods(undefined, context) : repository.getPeriods(undefined, context))]
      : await Promise.all(scopeIds.map(id => repository.getReportPeriods ? repository.getReportPeriods(id, context) : repository.getPeriods(id, context)));
    return [...new Set(pages.flat())].sort();
  }, [repository, scopeIds, pin, formatPending, purchaseType]));
  const chainMap = new Map(chains.data?.map(c => [c.id, c.name]) ?? []);
  const options = (chains.data ?? []).filter(c => c.has_history || showEmpty).map(c => ({ id: c.id, name: `${c.parentId && chainMap.has(c.parentId) ? chainMap.get(c.parentId) + " → " : ""}${c.name}${c.has_history ? "" : " · Sin histórico"}` }));
  const modelControl = <ModelMultiselect key={`${previewScopeKey(commercialScope)}/${filters.categoryId}/${filters.commercialType}/${filters.classificationStatus}`} value={filters.commercialModels ?? null} onChange={ids => setFilters(f => changeCommercial({ ...f, search: "" }, "commercialModels", ids))} options={commercialOptions(catalog, commercialScope, "commercialModels")} disabled={formatPending || commercial.loading || commercial.error || products.loading || products.error || !commercial.data}/>;
  const periodControls = <>
    <Picker label="Periodo desde" value={filters.periodRange[0]} onChange={id => setFilters(f => ({ ...f, periodRange: [id, f.periodRange[1] && id && f.periodRange[1] < id ? id : f.periodRange[1]] }))} options={(periods.data ?? []).map(p => ({ id: p, name: p }))} disabled={formatPending || periods.loading || periods.error} all="Desde el inicio"/>
    <Picker label="Periodo hasta" value={filters.periodRange[1]} onChange={id => setFilters(f => ({ ...f, periodRange: [f.periodRange[0] && id && f.periodRange[0] > id ? id : f.periodRange[0], id] }))} options={(periods.data ?? []).map(p => ({ id: p, name: p }))} disabled={formatPending || periods.loading || periods.error} all="Hasta el último"/>
  </>;
  const activeLabels = [
    filters.categoryId ? `Categoría: ${categories.data?.find(c => c.id === filters.categoryId)?.name ?? filters.categoryId}` : null,
    filters.commercialType, filters.commercialModels == null ? null : filters.commercialModels.join(" + ") || "Sin modelos seleccionados",
    filters.commercialColor, filters.commercialSize,
    filters.productId ? products.data?.find(p => p.id === filters.productId)?.description ?? filters.productId : null,
    filters.search ? `Búsqueda: ${filters.search}` : null,
    filters.classificationStatus ? `Clasificación: ${filters.classificationStatus === "APPROVED" ? "Aprobada" : "Pendiente"}` : null,
    filters.periodRange.some(Boolean) ? `Periodo ${filters.periodRange[0] ?? "inicio"} a ${filters.periodRange[1] ?? "último"}` : null,
  ].filter(Boolean);
  return <section aria-label="Filtros globales" className="mb-6 rounded-lg border border-slate-200 bg-white p-4">
    {compact && <div className="flex flex-wrap items-center justify-between gap-3"><div className="min-w-0 text-sm"><strong>{chainMap.get(filters.chainId ?? "") ?? "Todas las cadenas autorizadas"}{commercialNavigation ? ` · ${purchaseType}` : ""}</strong><p className="mt-1 break-words text-xs text-slate-500">{activeLabels.join(" · ") || "Todas las categorías y productos"}</p></div><Button variant="outline" size="sm" aria-expanded={opened} aria-controls="commercial-global-controls" onClick={() => setOpened(v => !v)}><SlidersHorizontal className="size-4"/>Filtros</Button></div>}
    <div id="commercial-global-controls" hidden={compact && !opened} className={compact ? "mt-4" : ""}>
    {commercialNavigation && <div className="mb-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
      <Picker label="Cadena" value={navigation.parentId} onChange={id => setFilters(f => ({ ...resetForChain(f, id), purchaseType: f.purchaseType ?? "RS", scopeChainIds: id ? commercialScopeNavigation(chains.data ?? [], id, showEmpty).scopeIds : undefined }))} options={navigation.chains.map(c => ({ id: c.id, name: c.name }))} disabled={chains.loading || chains.error} all={profile.global_role === "ADMIN" ? "Todas las cadenas" : "Todas las autorizadas"}/>
      <div className="space-y-2"><Label htmlFor="commercial-purchase-type">Tipo de compra</Label><Select value={purchaseType} onValueChange={value => setFilters(f => ({ ...resetForChain(f, f.chainId), scopeChainIds: f.scopeChainIds, purchaseType: value as "RS" | "CE" }))}><SelectTrigger id="commercial-purchase-type" aria-label="Tipo de compra" className="w-full bg-white"><SelectValue/></SelectTrigger><SelectContent><SelectItem value="RS">RS · Resurtible</SelectItem><SelectItem value="CE">CE · Compra Especial</SelectItem></SelectContent></Select></div>
      <Picker label="Formato" value={filters.scopeChainIds !== undefined ? null : filters.chainId} onChange={id => setFilters(f => ({ ...resetForChain(f, id ?? navigation.parentId), purchaseType: f.purchaseType ?? "RS", scopeChainIds: id ? undefined : navigation.scopeIds }))} options={navigation.formats.map(c => ({ id: c.id, name: `${c.name}${c.has_history ? "" : " · Sin histórico"}` }))} disabled={!navigation.parentId || chains.loading || chains.error} all="Todos los formatos"/>
      {modelControl}{periodControls}
    </div>}
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {!commercialNavigation && <Picker label="Cadena / unidad comercial" value={filters.chainId} onChange={id => setFilters(f => resetForChain(f, id))} options={options} disabled={chains.loading || chains.error} all={profile.global_role === "ADMIN" ? "Todas las cadenas" : "Todas las autorizadas"}/>}
      <Picker label="Categoría" value={filters.categoryId} onChange={id => setFilters(f => ({ ...f, categoryId: id, search: "", productId: null, commercialType: null, commercialModels: null, commercialColor: null, commercialSize: null }))} options={(categories.data ?? []).map(c => ({ id: c.id, name: (!filters.chainId || (scopeIds?.length ?? 0) > 1) && chainMap.has(c.chain_id) ? `${c.name} · ${chainMap.get(c.chain_id)}` : c.name }))} disabled={formatPending || categories.loading || categories.error}/>
      {([["commercialType", "Tipo"], ["commercialModels", "Modelo"], ["commercialColor", "Color"], ["commercialSize", "Tamaño / Presentación"]] as [CommercialFilter, string][]).map(([field, label]) => field === "commercialModels" ? !commercialNavigation && <div key={field}>{modelControl}</div> : <Picker key={field} label={label} value={filters[field] ?? null} onChange={id => setFilters(f => changeCommercial({ ...f, search: "" }, field, id))} options={commercialOptions(catalog, commercialScope, field)} disabled={formatPending || commercial.loading || commercial.error || products.loading || products.error || !commercial.data} all="Todos"/>)}
      <Picker label="Producto" value={filters.productId} onChange={id => setFilters(f => {
        if (!id) return { ...f, productId: null };
        const selected = matching.find(p => p.id === id);
        // Product identity includes its chain. A product picked from "Todas"
        // must never be sent to Railway without that exact scope.
        if (selected) return { ...f, chainId: selected.chain_id, scopeChainIds: undefined, categoryId: selected.category_id, productId: id };
        return f;
      })} options={matching.map(p => ({ id: p.id, name: `${p.description} · ${p.product_code}${p.variant_code ? " · " + p.variant_code : ""}${commercial.data && (!filters.chainId || (scopeIds?.length ?? 0) > 1) ? ` · ${chainMap.get(p.chain_id) ?? p.chain_id}` : ""}` }))} disabled={formatPending || products.loading || products.error || Boolean(filters.search && (searchedProducts.loading || searchedProducts.error))} all={`Todos los productos${commercial.data ? ` (${matching.length})` : ""}`}/>
      <div className="space-y-2"><Label htmlFor="product-search">Buscar producto, ITEM, UPC o variante</Label><Input id="product-search" type="search" maxLength={100} value={filters.search} disabled={formatPending} onChange={e => setFilters(f => ({ ...f, search: e.target.value, productId: null }))} placeholder="Buscar en catálogo autorizado"/></div>
    </div>
    <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Picker label="Clasificación comercial" value={filters.classificationStatus ?? null} onChange={id => setFilters(f => ({ ...f, classificationStatus: id, search: "", productId: null, commercialType: null, commercialModels: null, commercialColor: null, commercialSize: null }))} options={[{ id: "APPROVED", name: "Aprobada" }, { id: "PENDING_CLASSIFICATION", name: "Pendiente de clasificación" }]} disabled={formatPending || commercial.loading || commercial.error || !commercial.data}/>
      {!commercialNavigation && periodControls}
      <div className="flex flex-wrap items-end gap-4 pb-1"><label className="flex items-center gap-2 text-sm text-slate-600"><input type="checkbox" checked={showEmpty} onChange={e => setShowEmpty(e.target.checked)}/>Scopes sin histórico</label><Button variant="ghost" size="sm" onClick={() => setFilters({ ...emptyFilters, ...(commercialNavigation ? { purchaseType: "RS" as const } : {}) })}><RotateCcw className="size-4"/>Limpiar</Button></div>
    </div>
    </div>
    {formatPending ? <p className="mt-3 text-xs text-slate-600">Alcance comercial no disponible; se requiere una cadena autorizada con scopes identificados.</p> : commercial.data && <p className="mt-3 text-xs text-slate-600">{selectedProducts.length} productos coinciden{selectedProducts.some(p => p.commercial?.status !== "APPROVED") ? " · Clasificaciones comerciales provisionales" : ""}{filters.periodRange.some(Boolean) ? ` · Periodo ${filters.periodRange[0] ?? "inicio"} a ${filters.periodRange[1] ?? "último"}` : ""}</p>}
    {commercial.error && <p role="alert" className="mt-3 text-sm text-rose-700">No fue posible cargar la clasificación comercial.</p>}
    {[[chains.error, "las cadenas"], [categories.error, "las categorías"], [products.error || searchedProducts.error, "los productos"], [periods.error, "los periodos"]].map(([failed, control]) => failed && <p key={String(control)} role="alert" className="mt-3 text-sm text-rose-700">No fue posible cargar {control}.{control === "los periodos" && repository.getReadDiagnostic?.("getPeriods")?.code ? ` Código ${repository.getReadDiagnostic?.("getPeriods")?.code}.` : ""}</p>)}
  </section>;
}
function Intro({ title, copy }: { title: string; copy: string }) { return <div className="mb-5"><h1 className="text-2xl font-semibold tracking-tight text-slate-950 sm:text-3xl">{title}</h1><p className="mt-2 text-sm leading-6 text-slate-500">{copy}</p></div>; }
export function Dashboard() {
  const { repository, filters, preview, seriesVisibility, setSeriesVisibility } = useForecastFilters();
  const pin = useOperationPin();
  const currentSelection = useCurrentForecastSelection(repository.previews, filters.chainId, filters.productId);
  const summary = useRead(useCallback(async () => repository.getHistoricalSummary(filters, await pin()), [repository, filters, pin]));
  const scope = filters.chainId ? preview?.result?.scopes.find(s => s.chain_id === filters.chainId) ?? null : null;
  const validScope = currentRetrospectivePreview(preview?.result ?? null, scope) ? scope : null;
  const chartScope = validScope ?? (scope?.provisional_cold_start ? scope : null);
  const cutoff = String(chartScope?.issue_period ?? "");
  const historical = useRead(useCallback(async () => filters.chainId ? repository.getForecastHistory?.(filters, await pin()) ?? [] : [], [repository, filters, pin]));
  const customer = useRead(useCallback(async () => filters.chainId && cutoff ? repository.getCustomerForecast?.(filters, cutoff, await pin()) ?? [] : [], [repository, filters, cutoff, pin]));
  const gates = useRead(useCallback(() => filters.chainId && validScope && repository.previews?.qualityGates
    ? repository.previews.qualityGates(filters.chainId) : Promise.resolve(null),
    [repository, filters.chainId, validScope]));
  const quality = gates.data?.dataset_hash === validScope?.dataset_hash ? gates.data : null;
  const currentHorizons = selectedForecastHorizons(currentSelection);
  const chartCutoff = currentHorizons.length === 12 ? currentSelection?.issue_period.slice(0,7) : cutoff;
  const points = traderPoints(historical.data ?? [], customer.data ?? [], currentHorizons.length ? currentHorizons : scopeHorizons(validScope, filters), chartCutoff, filters, scopeProvisionalHorizons(chartScope, filters));
  return <div><Intro title="Dashboard ejecutivo" copy="Histórico publicado según tu acceso y los filtros seleccionados."/><ReadState loading={summary.loading} error={summary.error} empty={summary.data?.observationCount === 0}/>{summary.data && <>
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{[["Scopes con histórico", number(summary.data.scopeCount)], ["Productos visibles", number(summary.data.productCount)], ["Observaciones", number(summary.data.observationCount)], ["Último periodo disponible", summary.data.latestPeriod ?? "Sin dato"]].map(([label, value]) => <Card key={label} className="border-slate-200 shadow-sm"><CardContent className="p-5"><p className="text-sm text-slate-500">{label}</p><p className="mt-4 text-3xl font-semibold tracking-tight text-slate-950">{value}</p></CardContent></Card>)}</div>
    <div className="mt-5 space-y-5">{hasCommercialFilters(filters) ? <StoredScopeChart/> : filters.chainId ? <>
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
function StoredScopeChart() {
  const { repository, filters } = useForecastFilters();
  const pin = useOperationPin();
  const report = useRead(useCallback(async () => {
    if (!repository.getReportSnapshot || !repository.getReportHistory) throw new Error("report_unavailable");
    const context = await pin();
    const [snapshot, history, inventory] = await Promise.all([repository.getReportSnapshot(filters, context), repository.getReportHistory({ ...filters, periodRange: [null, null] }, context), context ? repository.getReportInventory?.(filters, context) ?? Promise.resolve([]) : Promise.resolve([])]);
    return periodsDashboard(snapshot, history, emptyReportChoice, filters.periodRange, inventory);
  }, [repository, filters, pin]));
  return <><ReadState loading={report.loading} error={report.error}/>{report.data && <><p className="text-xs text-slate-600">Forecast almacenado: {report.data.available}/{report.data.matrix.rows.length} productos · Con decisión: {report.data.adopted}{report.data.available > 0 && report.data.available < report.data.matrix.rows.length ? " · TOTAL PARCIAL" : ""}</p><PeriodsComparisonChart points={report.data.points} cutoff={report.data.cutoff}/></>}</>;
}
function DashboardAllScopesChart({ scopes }: { scopes: import("@/lib/supabase/types").Summary["byScope"] }) {
  const { repository, filters, seriesVisibility, setSeriesVisibility } = useForecastFilters();
  const pin = useOperationPin();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = scopes.find(row => row.chain.id === selectedId) ?? scopes[0];
  const chainId = selected?.chain.id ?? null;
  const chartFilters = useMemo<Filters>(() => ({ ...filters, chainId, productId: null }), [filters, chainId]);
  const history = useRead(useCallback(async () => chainId ? repository.getForecastHistory?.(chartFilters, await pin()) ?? [] : [], [repository, chainId, chartFilters, pin]));
  const preview = useRead(useCallback(async () => chainId ? repository.previews?.latest(chainId, null, await pin()) ?? null : null, [repository, chainId, pin]));
  const scope = preview.data?.status === "READY_PREVIEW" ? preview.data.scopes.find(row => row.chain_id === chainId) ?? null : null;
  const validScope = currentRetrospectivePreview(preview.data ?? null, scope) ? scope : null;
  const cutoff = String(validScope?.issue_period ?? history.data?.at(-1)?.period ?? "");
  const customer = useRead(useCallback(async () => chainId && cutoff ? repository.getCustomerForecast?.(chartFilters, cutoff, await pin()) ?? [] : [], [repository, chainId, chartFilters, cutoff, pin]));
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
  return <CommercialHistoryView/>;
}
function QualityView() { return <OperationalQualityView/>; }
export function UsersView() {
  const { repository, profile } = useForecastFilters();
  if (profile.global_role !== "ADMIN") return null;
  return <UserAdministrationView repository={repository} profile={profile}/>;
}
function DataTable({ headers, rows }: { headers: string[]; rows: string[][] }) { return <div className="overflow-hidden rounded-xl border border-slate-200 bg-white"><div className="overflow-x-auto"><Table><TableHeader><TableRow className="bg-slate-50">{headers.map(h => <TableHead className="whitespace-nowrap font-semibold text-slate-700" key={h}>{h}</TableHead>)}</TableRow></TableHeader><TableBody>{rows.map((row, i) => <TableRow key={i}>{row.map((cell, j) => <TableCell key={j} className={j < 3 ? "min-w-24 text-slate-700" : "whitespace-nowrap text-slate-600"}>{cell}</TableCell>)}</TableRow>)}</TableBody></Table></div></div>; }
