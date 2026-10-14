"use client";

import { useCallback, useMemo, useState } from "react";
import { buildForecastMatrix, emptyReportChoice, matrixTotal, reportSeries, temporalLabel, type ForecastMatrixRow, type ReportSeries, type ReportSnapshot } from "@/lib/forecast-reporting";
import { useForecastFilters, useRead } from "./forecast-towell-app";
import { CalculationFilters, ReportPaging, ReportSelect, ReportState, ReportTable, reportNumber, reportPercent } from "./report-controls";

export function ForecastPeriodsView() {
  const { repository, filters } = useForecastFilters();
  const report = useRead(useCallback(() => repository.getReportSnapshot ? repository.getReportSnapshot(filters) : Promise.reject(new Error("report_unavailable")), [repository, filters]));
  return <section className="space-y-5"><h1 className="text-2xl font-semibold text-slate-950">Matriz futura consolidada de forecast</h1><ReportState loading={report.loading} error={report.error}/>{report.data && <Matrix key={JSON.stringify(filters)} snapshot={report.data} periods={filters.periodRange}/>}</section>;
}
function Matrix({ snapshot, periods }: { snapshot: ReportSnapshot; periods: [string | null, string | null] }) {
  const [choice, setChoice] = useState({ ...emptyReportChoice });
  const [series, setSeries] = useState<ReportSeries | "COMPARE">("COMPARE");
  const matrix = useMemo(() => {
    try { return { data: buildForecastMatrix(snapshot, choice, periods), error: false }; }
    catch { return { data: null, error: true }; }
  }, [snapshot, choice, periods]);
  const shown = series === "COMPARE" ? Object.keys(reportSeries) as ReportSeries[] : [series];
  const data = matrix.data;
  const groups = data ? [...new Set(data.rows.map(r => r.product.chain_id))].flatMap(chain => {
    const rows = data.rows.filter(r => r.product.chain_id === chain);
    return [{ id: chain, label: rows[0].chain, rows }, ...[...new Set(rows.map(r => r.product.category_id))].map(category => ({ id: `${chain}/${category}`, label: `${rows[0].chain} / ${rows.find(r => r.product.category_id === category)!.category}`, rows: rows.filter(r => r.product.category_id === category) }))];
  }) : [];
  return <><CalculationFilters snapshot={snapshot} choice={choice} onChange={setChoice} decisions/><ReportSelect label="Comparar series" value={series} onChange={v => setSeries(v as typeof series)} options={[{ value: "COMPARE", label: "Todas las alternativas" }, ...Object.entries(reportSeries).map(([value, label]) => ({ value, label }))]}/>
    <ReportState loading={false} error={matrix.error} empty={data?.periods.length === 0}/>
    {data && data.periods.length > 0 && <><div className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-slate-600"><span>Corte: <strong>{data.issue}</strong></span><span>Productos: <strong>{data.rows.length}</strong></span><span>Scopes padre excluidos: <strong>{data.excludedParentScopes}</strong></span><span>Forecast disponible: <strong>{data.rows.filter(r => data.periods.some(p => ["STATISTICAL", "ML", "ENSEMBLE", "COLD_START"].some(s => r.values[p][s as ReportSeries] !== null))).length}/{data.rows.length}</strong></span><span>Adoptado: <strong>{data.rows.filter(r => r.adopted).length}/{data.rows.length}</strong></span></div>
      <ProductPages key={`${choice.version}/${choice.issue}/${choice.decision}`} rows={data.rows} periods={data.periods} shown={shown} issue={data.issue!}/>
      <h2 className="text-lg font-semibold">Totales por categoría, cadena y mes</h2>
      <ReportTable label="Totales de forecast" headers={["Scope", "Serie", ...data.periods]} rows={[{ id: "all", label: "Total consolidado", rows: data.rows }, ...groups].flatMap(g => shown.map(s => ({ id: `${g.id}/${s}`, cells: [g.label, reportSeries[s], ...data.periods.map(p => { const t = matrixTotal(g.rows, p, s); return <div key={p} className="whitespace-nowrap"><strong>{reportNumber(t.value)}</strong><p className="mt-1 text-xs text-slate-500">Cobertura {t.covered}/{t.eligible}{t.covered && t.covered < t.eligible ? " · Total parcial" : ""}</p></div>; })] })))}/>
    </>}
  </>;
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
