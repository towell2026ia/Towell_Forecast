"use client";

import { useCallback, useMemo, useState } from "react";
import { addReportMonth, emptyReportChoice, matrixTotal, reportSeries, temporalLabel, type ForecastMatrixRow, type ReportSnapshot } from "@/lib/forecast-reporting";
import type { Filters, HistoricalRow } from "@/lib/supabase/types";
import { detailMetrics, periodsDashboard, shortPeriod } from "@/lib/periods-dashboard";
import { modelKey } from "@/lib/commercial-filters";
import { PeriodsComparisonChart } from "@/components/forecast/periods-comparison-chart";
import { useForecastFilters, useRead } from "./forecast-towell-app";
import { CalculationFilters, ReportPaging, ReportSelect, ReportState, ReportTable, reportNumber, reportPercent } from "./report-controls";

const candidates = ["STATISTICAL", "ML", "ENSEMBLE"] as const;
const exactNumber = (n: number | null) => n === null ? "Sin dato" : n.toLocaleString("es-MX", { maximumFractionDigits: 20 });
const modelName = (r: ForecastMatrixRow) => r.product.commercial?.model ? modelKey(r.product.commercial.model) : "Sin identificar";

export function ForecastPeriodsView() {
  const { repository, filters } = useForecastFilters();
  const report = useRead(useCallback(async () => {
    if (!repository.getReportSnapshot) throw new Error("report_unavailable");
    // Read the authorized historical comparison years too. The range masks
    // positions in the presentation; it never changes the calculation's cut.
    const [snapshot, history] = await Promise.all([repository.getReportSnapshot(filters), repository.getReportHistory?.({ ...filters, periodRange: [null, null] }) ?? Promise.resolve([])]);
    return { snapshot, history };
  }, [repository, filters]));
  return <section className="min-w-0 space-y-5"><div><h1 className="text-2xl font-semibold text-slate-950">Periodos</h1><p className="mt-1 text-sm text-slate-500">Desempeño comercial y forecast almacenado</p></div><ReportState loading={report.loading} error={report.error}/>{report.data && <Matrix key={JSON.stringify(filters)} snapshot={report.data.snapshot} history={report.data.history} periods={filters.periodRange}/>}</section>;
}
function Matrix({ snapshot, history, periods }: { snapshot: ReportSnapshot; history: HistoricalRow[]; periods: Filters["periodRange"] }) {
  const [choice, setChoice] = useState({ ...emptyReportChoice });
  const matrix = useMemo(() => {
    try { return { data: periodsDashboard(snapshot, history, choice, periods), error: false }; }
    catch { return { data: null, error: true }; }
  }, [snapshot, history, choice, periods]);
  const dashboard = matrix.data, data = dashboard?.matrix;
  const ready = data?.rows.filter(r => r.calculation) ?? [];
  const pending = data?.rows.filter(r => !r.calculation) ?? [];
  return <>
    <CalculationFilters snapshot={snapshot} choice={choice} onChange={setChoice} decisions/>
    <ReportSelect label="Cálculo de comparación" value={choice.calculationId ?? "compatible"} onChange={id => setChoice({ ...choice, calculationId: id === "compatible" ? null : id, version: null, issue: null })} options={[{ value: "compatible", label: "Consolidado de cálculos compatibles" }, ...snapshot.calculations.map(c => ({ value: c.id, label: `${c.calculation_code} · ${snapshot.products.find(p => p.id === c.product_id)?.description ?? c.product_id} · Corte ${c.issue_period.slice(0, 7)} · ${c.id}` }))]}/>
    <ReportState loading={false} error={matrix.error}/>
    {dashboard && data && <>
      <div aria-label="Alcance de Periodos" className="space-y-2"><p className="text-sm font-semibold text-slate-900">{[...new Set(data.rows.map(r => r.chain))].join(" · ") || "Sin productos coincidentes"}</p><p className="text-xs text-slate-600">{dashboard.coverage.length <= 5 ? dashboard.coverage.map(c => c.model).join(" + ") : `${dashboard.coverage.length} modelos`} · {data.rows.length} productos · Corte {dashboard.cutoff ?? "Sin dato"}{!data.issue ? " (histórico; sin cálculo)" : ""}</p><p className="text-xs text-slate-500">{data.excludedParentScopes} scopes padre superpuestos excluidos. Las curvas se consolidan por corte, objetivo, versión de motor y modo de evidencia compatibles.</p></div>
      <dl aria-label="Indicadores comerciales" className="grid grid-cols-2 gap-y-5 border-y border-slate-200 py-5 lg:grid-cols-6">{[["Venta real · 9 meses", reportNumber(dashboard.kpis.sales)], ["Pedido · 9 meses", reportNumber(dashboard.kpis.orders)], ["Enviado · 9 meses", reportNumber(dashboard.kpis.delivery)], ["Fill Rate", reportPercent(dashboard.kpis.fillRate)], ["Productos con cálculo / Total", `${dashboard.available}/${data.rows.length}`], ["Con decisión / Total", `${dashboard.adopted}/${data.rows.length}`]].map(([label, value]) => <div key={label} className="min-w-0 border-l border-slate-200 px-3 first:border-l-0"><dt className="text-xs text-slate-500">{label}</dt><dd className="mt-2 break-words text-xl font-semibold tabular-nums text-slate-950">{value}</dd></div>)}</dl>
      {ready.length > 0 && ready.length < data.rows.length && <p role="status" className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900"><strong>TOTAL PARCIAL</strong> · Forecast de {ready.length}/{data.rows.length} productos del alcance. No representa el total completo de los modelos seleccionados.</p>}
      {ready.some(r => r.calculation?.evidence_mode === "RETROSPECTIVE_TRAINING" || data.periods.some(p => temporalLabel(p, r.calculation?.created_at ?? null).includes("Emisión retrospectiva"))) && <p className="text-sm text-amber-800"><strong>Emisión retrospectiva.</strong> Candidatos congelados; referencia sugerida no implica adopción. Los meses emitidos retrospectivamente no generan WAPE LIVE.</p>}
      <PeriodsComparisonChart points={dashboard.points} cutoff={dashboard.cutoff}/>
      <div aria-label="Cobertura comercial" className="flex flex-wrap gap-x-6 gap-y-2 text-xs text-slate-600"><span>Real: {dashboard.actualMonths}/9 meses con dato</span><span>Año −2: {dashboard.priorComplete[0]}/12 meses completos</span><span>Año −1: {dashboard.priorComplete[1]}/12 meses completos</span><span>Fill Rate: {dashboard.kpis.paired} pares Pedido/Enviado</span><span>Sin dato no equivale a cero</span></div>
      {dashboard.cutoff && <p className="text-xs text-slate-500">Año −1: {addReportMonth(dashboard.cutoff, -20)} a {addReportMonth(dashboard.cutoff, -9)} · Año −2: {addReportMonth(dashboard.cutoff, -32)} a {addReportMonth(dashboard.cutoff, -21)}</p>}
      <details open={dashboard.coverage.length <= 5} className="text-sm"><summary className="cursor-pointer font-medium">Cobertura por modelo ({dashboard.coverage.length})</summary><div className="mt-3"><ReportTable label="Cobertura por modelo" headers={["Modelo", "Con cálculo", "Sin cálculo compatible", "Con decisión", "Sin decisión", "Total"]} rows={dashboard.coverage.map(c => ({ id: c.model, cells: [c.model, c.calculated, c.pending, c.decided, c.undecided, c.total] }))}/></div></details>
      <CompactMatrix key={JSON.stringify(choice)} rows={ready} scope={data.rows} periods={data.periods} issue={data.issue}/>
      <PendingProducts key={`pending/${JSON.stringify(choice)}`} rows={pending} snapshot={snapshot}/>
      <details className="space-y-4 border-t border-slate-200 py-5"><summary className="cursor-pointer text-lg font-semibold">Detalle técnico expandible · C1–Cn</summary><p className="text-xs text-slate-500">Resultados almacenados, ganadores, WAPE, Bias, bandas, corte, emisión, versiones, Dataset Hash y trazabilidad por producto.</p>{snapshot.calculations.map(c => <details key={c.id} className="rounded-md border border-slate-200 bg-white p-3"><summary className="cursor-pointer break-words text-sm">{c.calculation_code} · {snapshot.products.find(p => p.id === c.product_id)?.description ?? c.product_id} · {c.issue_period.slice(0, 7)} · {c.status}</summary><pre className="mt-3 max-h-[36rem] overflow-auto whitespace-pre-wrap break-all text-xs">{JSON.stringify({ calculation: c, horizons: snapshot.horizons.filter(h => h.calculation_id === c.id), decisions: snapshot.decisions.filter(e => e.calculation_id === c.id), classification: snapshot.products.find(p => p.id === c.product_id)?.commercial }, null, 2)}</pre></details>)}{!snapshot.calculations.length && <p className="text-sm text-slate-500">Sin cálculos almacenados para este alcance.</p>}</details>
    </>}
  </>;
}
function CompactMatrix({ rows, scope, periods, issue }: { rows: ForecastMatrixRow[]; scope: ForecastMatrixRow[]; periods: string[]; issue: string | null }) {
  const [id, setId] = useState(rows.length === 1 ? rows[0].product.id : "all");
  const row = rows.find(r => r.product.id === id);
  const selected = row ? [row] : scope;
  return <section className="min-w-0 space-y-4 border-t border-slate-200 py-5"><div><h2 className="text-lg font-semibold">Matriz compacta H1–H12</h2><p className="mt-1 text-xs text-slate-500">Tres candidatos congelados · valores almacenados · sin adopción automática</p></div>
    {rows.length > 1 && <ReportSelect label="Producto de la matriz" value={id} onChange={setId} options={[{ value: "all", label: "Total compatible · ver cobertura del alcance" }, ...rows.map(r => ({ value: r.product.id, label: `${r.chain} · ${r.product.description} · ${r.calculation!.calculation_code}` }))]}/>}
    {row && <><dl aria-label="Identidad del cálculo" className="grid gap-x-5 gap-y-3 rounded-md bg-slate-50 p-4 text-sm sm:grid-cols-2 lg:grid-cols-3">{[["Cadena", row.chain], ["Modelo", modelName(row)], ["Color", row.product.commercial?.color ?? "Sin identificar"], ["Producto", row.product.description], ["Calculation Code", row.calculation!.calculation_code], ["Issue Period", row.calculation!.issue_period], ["Created At", row.calculation!.created_at], ["Estado del cálculo", row.calculation!.status], ["Referencia sugerida", row.calculation!.suggested_reference?.model ?? "Sin referencia almacenada"], ["Estado de decisión", row.adopted ? `Adoptado · ${row.adopted.selected_candidate}` : "Sin decisión"]].map(([label, value]) => <div key={label} className="min-w-0"><dt className="text-xs text-slate-500">{label}</dt><dd className="mt-1 break-words font-medium">{value}</dd></div>)}</dl><dl className="grid gap-3 sm:grid-cols-3">{detailMetrics(row).map(m => <div key={m.family}><dt className="text-xs text-slate-500">WAPE retrospectivo · {reportSeries[m.family]}</dt><dd className="text-lg font-semibold">{reportPercent(m.wape)}</dd></div>)}</dl></>}
    {rows.length > 0 && periods.length > 0 ? <ReportTable label="Matriz compacta de forecast" headers={["Periodo", "Horizonte", "Estadístico", "Machine Learning", "Ensemble", ...(selected.some(r => r.adopted) ? ["Towell adoptado"] : [])]} rows={periods.map(p => ({ id: p, cells: [<span key="period" title={p}>{shortPeriod(p)}</span>, `H${(Number(p.slice(0, 4)) - Number(issue!.slice(0, 4))) * 12 + Number(p.slice(5, 7)) - Number(issue!.slice(5, 7))}`, ...[...candidates, ...(selected.some(r => r.adopted) ? ["TOWELL" as const] : [])].map(s => { const q = matrixTotal(selected, p, s); return <span key={s} title={`Cobertura ${q.covered}/${q.eligible}`} className="whitespace-nowrap tabular-nums">{exactNumber(q.value)}</span>; })] }))}/> : <p className="text-sm text-slate-500">Sin cálculo compatible disponible para los periodos seleccionados.</p>}
    {row && <p className="break-all text-xs text-slate-500">Product ID: {row.product.id} · Calculation ID: {row.calculation!.id} · {row.product.commercial?.status ?? "PENDING_CLASSIFICATION"}</p>}
  </section>;
}
function PendingProducts({ rows, snapshot }: { rows: ForecastMatrixRow[]; snapshot: ReportSnapshot }) {
  const [page, setPage] = useState(1), [size, setSize] = useState<50 | 100 | 250>(50);
  return <details className="space-y-4 border-t border-slate-200 py-5"><summary className="cursor-pointer text-lg font-semibold">Productos pendientes de cálculo ({rows.length})</summary><p className="text-xs text-slate-500">Permanecen en el alcance. Cambiar filtros no ejecuta motores.</p><ReportTable label="Productos pendientes de cálculo" headers={["Producto", "Modelo", "Color", "Tamaño", "Estado", "Motivo"]} rows={rows.slice((page - 1) * size, page * size).map(r => ({ id: r.product.id, cells: [<div key="product">{r.product.description}<p className="mt-1 text-xs text-slate-500">{r.chain} · {r.product.product_code} · {r.product.id}</p></div>, modelName(r), r.product.commercial?.color ?? "Sin identificar", r.product.commercial?.size ?? (r.product.commercial?.not_applicable?.includes("size") ? "No aplica (documentado)" : "Sin identificar"), r.product.commercial?.status ?? "PENDING_CLASSIFICATION", snapshot.calculations.some(c => c.product_id === r.product.id && c.chain_id === r.product.chain_id) ? "Cálculo almacenado fuera del grupo compatible seleccionado" : "Sin cálculo almacenado en la lectura autorizada"] }))}/><ReportPaging page={page} previous={() => setPage(p => p - 1)} next={() => setPage(p => p + 1)} hasMore={rows.length > page * size} loading={false} size={size} setSize={v => { setSize(v); setPage(1); }}/></details>;
}
