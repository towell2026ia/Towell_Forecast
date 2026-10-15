"use client";

import { useCallback, useMemo, useState } from "react";
import { emptyReportChoice, matrixTotal, reportSeries, temporalLabel, type ForecastMatrixRow, type ReportSeries, type ReportSnapshot } from "@/lib/forecast-reporting";
import type { Filters, HistoricalRow } from "@/lib/supabase/types";
import { detailMetrics, periodsDashboard } from "@/lib/periods-dashboard";
import { PeriodsComparisonChart } from "@/components/forecast/periods-comparison-chart";
import { useForecastFilters, useRead } from "./forecast-towell-app";
import { CalculationFilters, ReportPaging, ReportSelect, ReportState, ReportTable, reportNumber, reportPercent } from "./report-controls";

export function ForecastPeriodsView() {
  const { repository, filters } = useForecastFilters();
  const report = useRead(useCallback(async () => {
    if (!repository.getReportSnapshot) throw new Error("report_unavailable");
    const [snapshot, history] = await Promise.all([repository.getReportSnapshot(filters), repository.getReportHistory?.(filters) ?? Promise.resolve([])]);
    return { snapshot, history };
  }, [repository, filters]));
  return <section className="min-w-0 space-y-5"><div><h1 className="text-2xl font-semibold text-slate-950">Periodos</h1><p className="mt-1 text-sm text-slate-500">Desempeño comercial y forecast disponible</p></div><ReportState loading={report.loading} error={report.error}/>{report.data && <Matrix key={JSON.stringify(filters)} snapshot={report.data.snapshot} history={report.data.history} periods={filters.periodRange}/>}</section>;
}
function Matrix({ snapshot, history, periods }: { snapshot: ReportSnapshot; history: HistoricalRow[]; periods: Filters["periodRange"] }) {
  const [choice, setChoice] = useState({ ...emptyReportChoice });
  const [series, setSeries] = useState<ReportSeries | "COMPARE">("COMPARE");
  const years = [...new Set([...history.map(r => Number(r.period.slice(0, 4))), ...snapshot.calculations.map(c => Number(c.issue_period.slice(0, 4)))])].sort((a, b) => b - a);
  const [year, setYear] = useState(years[0] ?? Number(new Intl.DateTimeFormat("en", { timeZone: "America/Mexico_City", year: "numeric" }).format(new Date())));
  const matrix = useMemo(() => {
    try { return { data: periodsDashboard(snapshot, history, choice, periods, year), error: false }; }
    catch { return { data: null, error: true }; }
  }, [snapshot, history, choice, periods, year]);
  const shown = series === "COMPARE" ? Object.keys(reportSeries) as ReportSeries[] : [series];
  const dashboard = matrix.data;
  const data = dashboard?.matrix;
  const groups = data ? [...new Set(data.rows.map(r => r.product.chain_id))].flatMap(chain => {
    const rows = data.rows.filter(r => r.product.chain_id === chain);
    return [{ id: chain, label: rows[0].chain, rows }, ...[...new Set(rows.map(r => r.product.category_id))].map(category => ({ id: `${chain}/${category}`, label: `${rows[0].chain} / ${rows.find(r => r.product.category_id === category)!.category}`, rows: rows.filter(r => r.product.category_id === category) }))];
  }) : [];
  return <><div className="grid items-end gap-4 lg:grid-cols-4"><div className="lg:col-span-3"><CalculationFilters snapshot={snapshot} choice={choice} onChange={setChoice} decisions/></div><ReportSelect label="Año comparativo" value={String(year)} onChange={v => setYear(Number(v))} options={(years.length ? years : [year]).map(y => ({ value: String(y), label: String(y) }))}/></div>
    <ReportState loading={false} error={matrix.error}/>
    {dashboard && data && <>
      <p className="text-xs text-slate-500">Corte {data.issue ?? "Sin cálculo"} · {data.rows.length} productos · {data.excludedParentScopes} scopes padre excluidos</p>
      <dl aria-label="Indicadores comerciales" className="grid grid-cols-2 gap-y-5 border-y border-slate-200 py-5 lg:grid-cols-6">
        {[["Venta real ene–sep", reportNumber(dashboard.kpis.sales)], ["Pedido ene–sep", reportNumber(dashboard.kpis.orders)], ["Enviado ene–sep", reportNumber(dashboard.kpis.delivery)], ["Fill Rate", reportPercent(dashboard.kpis.fillRate)], ["Forecast disponible", `${dashboard.available}/${data.rows.length}`], ["Adoptado", `${dashboard.adopted}/${data.rows.length}`]].map(([label, value]) => <div key={label} className="min-w-0 border-l border-slate-200 px-3 first:border-l-0"><dt className="text-xs text-slate-500">{label}</dt><dd className="mt-2 break-words text-xl font-semibold tabular-nums text-slate-950">{value}</dd></div>)}
      </dl>
      <PeriodsComparisonChart points={dashboard.points} year={year}/>
      <div aria-label="Cobertura comercial" className="flex flex-wrap gap-x-6 gap-y-2 text-xs text-slate-600"><span>Real {year}: {dashboard.actualMonths}/9 meses con dato</span><span>{year - 2}: {dashboard.priorComplete[0]}/12 meses completos</span><span>{year - 1}: {dashboard.priorComplete[1]}/12 meses completos</span><span>Fill Rate: {dashboard.kpis.paired} pares Pedido/Enviado</span><span>Sin dato no equivale a cero · Forecast disponible no implica adopción</span></div>
    </>}
    {data && data.periods.length > 0 && <>
      <div className="flex flex-wrap items-end justify-between gap-4"><div><h2 className="text-lg font-semibold">Matriz H1–H12</h2><p className="mt-1 text-xs text-slate-500">Forecast almacenado · WAPE retrospectivo en el detalle de cálculo</p></div><ReportSelect label="Comparar series" value={series} onChange={v => setSeries(v as typeof series)} options={[{ value: "COMPARE", label: "Todas las alternativas" }, ...Object.entries(reportSeries).map(([value, label]) => ({ value, label }))]}/></div>
      <ProductPages key={`pages/${choice.version}/${choice.issue}/${choice.decision}`} rows={data.rows} periods={data.periods} shown={shown} issue={data.issue!}/>
      <ProductDetail key={`detail/${choice.version}/${choice.issue}/${choice.decision}`} rows={data.rows} periods={data.periods}/>
      <h2 className="text-lg font-semibold">Totales por categoría, cadena y mes</h2>
      <ReportTable label="Totales de forecast" headers={["Scope", "Serie", ...data.periods]} rows={[{ id: "all", label: "Total consolidado", rows: data.rows }, ...groups].flatMap(g => shown.map(s => ({ id: `${g.id}/${s}`, cells: [g.label, reportSeries[s], ...data.periods.map(p => { const t = matrixTotal(g.rows, p, s); return <div key={p} className="whitespace-nowrap"><strong>{reportNumber(t.value)}</strong><p className="mt-1 text-xs text-slate-500">Cobertura {t.covered}/{t.eligible}{t.covered && t.covered < t.eligible ? " · Total parcial" : ""}</p></div>; })] })))}/>
    </>}
    {data && !data.periods.length && <p className="border-t border-slate-200 py-5 text-sm text-slate-500">Sin forecast almacenado para estos periodos. El histórico comercial permanece disponible.</p>}
  </>;
}
function ProductDetail({ rows, periods }: { rows: ForecastMatrixRow[]; periods: string[] }) {
  const [id, setId] = useState("");
  const row = rows.find(r => r.product.id === id);
  return <section className="space-y-4 border-t border-slate-200 py-5"><h2 className="text-lg font-semibold">Detalle por producto</h2><ReportSelect label="Producto del detalle" value={id} onChange={setId} options={[{ value: "", label: "Elegir producto" }, ...rows.map(r => ({ value: r.product.id, label: `${r.product.description} · ${r.chain} · ${r.product.product_code}` }))]}/>{row && <>
    <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm"><span>{row.chain} / {row.category}</span><span>{row.calculation?.calculation_code ?? "Sin cálculo"}</span><span>{row.product.commercial?.status === "APPROVED" ? "Clasificación aprobada" : "PENDING_CLASSIFICATION"}</span></div>
    <dl className="grid gap-3 sm:grid-cols-3">{detailMetrics(row).map(m => <div key={m.family}><dt className="text-xs text-slate-500">WAPE retrospectivo · {reportSeries[m.family]}</dt><dd className="text-lg font-semibold">{reportPercent(m.wape)}</dd></div>)}</dl>
    <ReportTable label="Detalle de alternativas por producto" headers={["Periodo", "H", ...Object.values(reportSeries)]} rows={periods.map(p => ({ id: p, cells: [p, row.calculation ? (Number(p.slice(0, 4)) - Number(row.calculation.issue_period.slice(0, 4))) * 12 + Number(p.slice(5)) - Number(row.calculation.issue_period.slice(5, 7)) : "Sin dato", ...Object.keys(reportSeries).map(s => reportNumber(row.values[p][s as ReportSeries]))] }))}/>
    <p className="break-all text-xs text-slate-500">Product ID: {row.product.id} · Calculation ID: {row.calculation?.id ?? "Sin dato"} · Referencia sugerida: {row.calculation?.suggested_reference?.model ?? "Sin dato"}</p>
  </>}</section>;
}
function ProductPages({ rows, periods, shown, issue }: { rows: ForecastMatrixRow[]; periods: string[]; shown: ReportSeries[]; issue: string }) {
  const [page, setPage] = useState(1), [size, setSize] = useState<50 | 100 | 250>(50);
  const horizon = (period: string) => (Number(period.slice(0, 4)) - Number(issue.slice(0, 4))) * 12 + Number(period.slice(5, 7)) - Number(issue.slice(5, 7));
  const headers = ["Producto / Código", "Cadena / Categoría", "Cálculo / Evidencia", ...periods.map(p => <div key={p}>{p}<p className="mt-1 text-xs font-normal text-slate-500">H{horizon(p)} · {temporalLabel(p, null).split(" · ")[0]}</p></div>)];
  const records = rows.slice((page - 1) * size, page * size).map(r => {
    const reference = r.calculation?.suggested_reference;
    const family = reference?.family?.toUpperCase() as ReportSeries | undefined;
    return { id: `${r.product.chain_id}/${r.product.id}`, cells: [
      <div key="product" className="min-w-48 max-w-64 break-words"><strong>{r.product.description}</strong><p className="mt-1 text-xs">{r.product.product_code}{r.product.variant_code ? ` / ${r.product.variant_code}` : ""}</p></div>,
      <div key="scope" className="min-w-40">{r.chain}<p className="mt-1 text-xs text-slate-500">{r.category}</p></div>,
      <div key="lineage" className="min-w-56 max-w-72 break-words">
        <strong>{r.calculation?.calculation_code ?? (r.provisional ? "Cold Start provisional" : "Sin cálculo")}</strong>
        <p className="text-xs">Corte {r.calculation?.issue_period.slice(0, 7) ?? r.provisional?.issue_period ?? "Sin dato"}</p>
        <p className="text-xs">Creado {r.calculation?.created_at ?? r.provisional?.created_at ?? "Sin dato"}</p>
        <p className="text-xs">{r.adopted ? `Adoptado ${r.adopted.selected_at}` : "Sin decisión"}</p>
        <details className="mt-2 text-xs"><summary className="cursor-pointer">Trazabilidad y WAPE retrospectivo</summary>
          <p className="mt-2 break-all">{r.calculation?.id ?? r.provisional?.job_id ?? "Sin dato"}</p>
          {(["STATISTICAL", "ML", "ENSEMBLE"] as const).map(f => <p key={f}>{reportSeries[f]}: {reportPercent(r.calculation?.candidate_metrics[f]?.wape)}</p>)}
          <p>Referencia sugerida: {family ? reportSeries[family] : "Sin dato"}{reference?.model ? ` · ${reference.model}` : ""}</p>
        </details>
      </div>,
      ...periods.map(p => <div key={p} className="min-w-44">
        <dl className="space-y-2">{shown.map(s => <div key={s}><dt className="text-xs text-slate-500">{reportSeries[s]}</dt><dd className="font-medium tabular-nums">{reportNumber(r.values[p][s])}</dd></div>)}</dl>
        <p className="mt-3 max-w-44 text-xs text-slate-500">{temporalLabel(p, r.calculation?.created_at ?? r.provisional?.created_at ?? null)}</p>
      </div>),
    ] };
  });
  return <><ReportState loading={false} error={false} empty={!rows.length}/>{rows.length > 0 && <ReportTable label="Forecast H1-H12 por producto" headers={headers} rows={records}/>}
    <ReportPaging page={page} previous={() => setPage(p => p - 1)} next={() => setPage(p => p + 1)} hasMore={rows.length > page * size} loading={false} size={size} setSize={v => { setSize(v); setPage(1); }}/>
  </>;
}
