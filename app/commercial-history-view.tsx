"use client";

import { useCallback, useMemo, useState } from "react";
import { chooseCalculations, commercialRows, emptyReportChoice, leafScopeIds, reportSeries, type CommercialRow, type ReportChoice, type ReportSnapshot, type ReportSeries } from "@/lib/forecast-reporting";
import type { Cursor, Filters, ForecastReadRepository, HistoricalRow } from "@/lib/supabase/types";
import { useForecastFilters, useRead } from "./forecast-towell-app";
import { CalculationFilters, ReportPaging, ReportSelect, ReportState, ReportTable, reportNumber, reportPercent } from "./report-controls";

const noReport: ReportSnapshot = { products: [], chains: [], categories: [], calculations: [], horizons: [], decisions: [], live: [], customers: [], provisionals: [], capabilities: { vintage_persistence: false, official_publication: false, champion_publication: false } };
export function CommercialHistoryView() {
  const { repository, filters } = useForecastFilters();
  const report = useRead(useCallback(() => repository.getReportSnapshot ? repository.getReportSnapshot(filters) : Promise.resolve(noReport), [repository, filters]));
  return <section className="space-y-5"><h1 className="text-2xl font-semibold text-slate-950">Matriz histórica comercial</h1><ReportState loading={report.loading} error={report.error}/>{report.data && <History key={JSON.stringify(filters)} snapshot={report.data} repository={repository} filters={filters}/>}</section>;
}
function History({ snapshot, repository, filters }: { snapshot: ReportSnapshot; repository: ForecastReadRepository; filters: Filters }) {
  const [choice, setChoice] = useState({ ...emptyReportChoice });
  const [metric, setMetric] = useState<Exclude<ReportSeries, "COLD_START">>("TOWELL");
  const [source, setSource] = useState("history");
  const stored = useRead(useCallback(async () => {
    if (source !== "calculation") return null;
    const output: HistoricalRow[] = [];
    for (const c of chooseCalculations(snapshot, choice).values()) {
      const product = snapshot.products.find(p => p.id === c.product_id && p.chain_id === c.chain_id);
      if (!product) throw new Error("history_scope_invalid");
      const horizons = snapshot.horizons.filter(h => h.calculation_id === c.id).sort((a, b) => a.horizon - b.horizon);
      const actual = await repository.getHistoricalObservations({ ...filters, chainId: c.chain_id, productId: c.product_id, periodRange: [horizons[0].target_period.slice(0, 7), horizons.at(-1)!.target_period.slice(0, 7)] }, { size: 50 });
      if (actual.next) throw new Error("history_target_ambiguous");
      for (const h of horizons) {
        const period = h.target_period.slice(0, 7);
        if (filters.periodRange[0] && period < filters.periodRange[0] || filters.periodRange[1] && period > filters.periodRange[1]) continue;
        // A stored forecast target without an observation is a view row, never a new commercial fact.
        output.push(actual.rows.find(r => r.period.slice(0, 7) === period) ?? { chain_id: c.chain_id, product_id: c.product_id, period: h.target_period, product, chain: snapshot.chains.find(s => s.id === c.chain_id)?.name ?? c.chain_id, SALES: null, ORDER: null, DELIVERY: null, availability: "Sin observación comercial" });
      }
    }
    return output;
  }, [source, snapshot, choice, repository, filters]));
  const scopedFilters = useMemo(() => {
    if (!choice.version && !choice.issue) return filters;
    const selected = new Set([...chooseCalculations(snapshot, choice).values()].map(c => c.id));
    const periods = snapshot.horizons.filter(h => selected.has(h.calculation_id)).map(h => h.target_period.slice(0, 7)).sort();
    return { ...filters, periodRange: [filters.periodRange[0] && filters.periodRange[0] > (periods[0] ?? "") ? filters.periodRange[0] : periods[0] ?? "2099-12", filters.periodRange[1] && filters.periodRange[1] < (periods.at(-1) ?? "9999") ? filters.periodRange[1] : periods.at(-1) ?? "2000-01"] as Filters["periodRange"] };
  }, [filters, choice, snapshot]);
  const key = `${choice.version}/${choice.issue}/${source}`;
  return <><CalculationFilters snapshot={snapshot} choice={choice} onChange={setChoice}/><div className="grid gap-4 sm:grid-cols-2"><ReportSelect label="Periodos comerciales" value={source} onChange={setSource} options={[{ value: "history", label: "Histórico original" }, { value: "calculation", label: "Horizontes del cálculo" }]}/><ReportSelect label="Referencia de error y WAPE LIVE" value={metric} onChange={v => setMetric(v as typeof metric)} options={Object.entries(reportSeries).filter(([v]) => v !== "COLD_START").map(([value, label]) => ({ value, label }))}/></div>
    {source === "calculation" && <ReportState loading={stored.loading} error={stored.error}/>}
    {(source === "history" || stored.data) && <><CommercialPages key={key} snapshot={snapshot} repository={repository} filters={scopedFilters} choice={choice} metric={metric} stored={source === "calculation" ? stored.data : null}/><Performance snapshot={snapshot} repository={repository} filters={scopedFilters} choice={choice} metric={metric} stored={source === "calculation" ? stored.data : null}/></>}
  </>;
}
function CommercialPages({ snapshot, repository, filters, choice, metric, stored }: { snapshot: ReportSnapshot; repository: ForecastReadRepository; filters: Filters; choice: ReportChoice; metric: Exclude<ReportSeries, "COLD_START">; stored: HistoricalRow[] | null }) {
  const [size, setSize] = useState<50 | 100 | 250>(50), [cursors, setCursors] = useState<(Cursor | null)[]>([null]);
  const cursor = cursors.at(-1) ?? null, index = cursors.length - 1;
  const page = useRead(useCallback(async () => {
    const result = stored ? { rows: stored.slice(index * size, (index + 1) * size), next: stored.length > (index + 1) * size ? { chain_id: "", product_id: "", period: "" } : null }
      : filters.periodRange[0] && filters.periodRange[1] && filters.periodRange[0] > filters.periodRange[1] ? { rows: [], next: null }
      : await repository.getHistoricalObservations(filters, { size, cursor });
    return { ...result, rows: commercialRows(result.rows, snapshot, choice, metric).filter(r => !choice.version && !choice.issue || r.calculation) };
  }, [stored, index, size, filters, repository, cursor, snapshot, choice, metric]));
  return <><ReportState loading={page.loading} error={page.error} empty={page.data?.rows.length === 0}/>{page.data && page.data.rows.length > 0 && <ReportTable label="Histórico comercial" headers={["Periodo", "Cadena", "Producto", "Código", "Forecast Cliente", "Forecast Estadístico", "Forecast ML", "Forecast Ensemble", "Forecast Towell adoptado", "Pedido", "Venta real", "Enviado", "Error absoluto", "WAPE LIVE", "Fill Rate", "Estado del dato", "Versión del cálculo"]} rows={page.data.rows.map(r => ({ id: `${r.chain_id}/${r.product_id}/${r.period}`, cells: [r.period.slice(0, 7), r.chain, <span key="product" className="block min-w-44 max-w-64 break-words">{r.product.description}</span>, <div key="code" className="min-w-28">{r.product.product_code}<p className="text-xs text-slate-500">{[...new Set(r.product.identifiers.map(i => i[1]).filter(Boolean))].join(" / ")}</p></div>, ...[r.customer, r.statistical, r.ml, r.ensemble, r.adopted, r.ORDER, r.SALES, r.DELIVERY].map(reportNumber), <div key="error">{reportNumber(r.absoluteError)}<p className="text-xs text-slate-500">{r.absoluteError !== null && r.liveError === null ? "Comparación no LIVE" : ""}</p></div>, reportPercent(r.wapeLive), reportPercent(r.fillRate), <div key="state" className="min-w-56"><p>{r.availability}</p><p className="mt-1 text-xs text-slate-500">{r.evidence}</p></div>, r.calculation ? <details key="calc" className="min-w-52 max-w-72 break-words"><summary className="cursor-pointer">{r.calculation.calculation_code} · {r.calculation.issue_period.slice(0, 7)}</summary><p className="mt-2 text-xs break-all">{r.calculation.id}</p><p className="text-xs">Creado {r.calculation.created_at}</p><p className="text-xs">Objetivo {r.period.slice(0, 7)}</p></details> : "Sin dato"] }))}/>}
    <ReportPaging page={cursors.length} previous={() => setCursors(c => c.slice(0, -1))} next={() => setCursors(c => [...c, page.data!.next])} hasMore={Boolean(page.data?.next)} loading={page.loading} size={size} setSize={v => { setSize(v); setCursors([null]); }}/>
  </>;
}
type Totals = { rows: number; live: number; actual: number; error: number; orders: number; delivered: number; service: number };
function Performance({ snapshot, repository, filters, choice, metric, stored }: { snapshot: ReportSnapshot; repository: ForecastReadRepository; filters: Filters; choice: ReportChoice; metric: Exclude<ReportSeries, "COLD_START">; stored: HistoricalRow[] | null }) {
  const totals = useRead(useCallback(async () => {
    const buckets = new Map<string, Totals>();
    const leaves = leafScopeIds(snapshot.chains);
    const ingest = (rows: CommercialRow[]) => { for (const r of rows) {
      if (snapshot.chains.length && !leaves.has(r.chain_id) || (choice.version || choice.issue) && !r.calculation) continue;
      for (const key of ["Acumulado", r.period.slice(0, 7)]) {
        const t = buckets.get(key) ?? { rows: 0, live: 0, actual: 0, error: 0, orders: 0, delivered: 0, service: 0 };
        t.rows++;
        if (r.liveActual !== null && r.liveError !== null) { t.live++; t.actual += r.liveActual; t.error += r.liveError; }
        if (r.ORDER !== null && r.DELIVERY !== null) { t.service++; t.orders += r.ORDER; t.delivered += r.DELIVERY; }
        buckets.set(key, t);
      }
    } };
    if (stored) ingest(commercialRows(stored, snapshot, choice, metric));
    else if (!(filters.periodRange[0] && filters.periodRange[1] && filters.periodRange[0] > filters.periodRange[1])) {
      let cursor: Cursor | null = null;
      for (let pages = 0; pages < 800; pages++) {
        const page = await repository.getHistoricalObservations(filters, { size: 250, cursor });
        ingest(commercialRows(page.rows, snapshot, choice, metric));
        if (!page.next) return buckets;
        if (JSON.stringify(page.next) === JSON.stringify(cursor)) throw new Error("history_cursor_invalid");
        cursor = page.next;
      }
      throw new Error("history_summary_limit");
    }
    return buckets;
  }, [stored, snapshot, choice, metric, repository, filters]));
  return <div className="space-y-3"><h2 className="text-lg font-semibold">Desempeño mensual y acumulado · Scopes hoja</h2><ReportState loading={totals.loading} error={totals.error} empty={totals.data?.size === 0}/>{totals.data && totals.data.size > 0 && <ReportTable label="Desempeño comercial" headers={["Periodo", "Filas", "Evidencia LIVE", "WAPE LIVE", "Pedido / Enviado", "Fill Rate"]} rows={[...totals.data].sort(([a], [b]) => a === "Acumulado" ? -1 : b === "Acumulado" ? 1 : a.localeCompare(b)).map(([id, t]) => ({ id, cells: [id, reportNumber(t.rows), reportNumber(t.live), reportPercent(t.actual > 0 ? t.error / t.actual * 100 : null), t.service ? `${reportNumber(t.orders)} / ${reportNumber(t.delivered)}` : "Sin dato", reportPercent(t.orders > 0 ? t.delivered / t.orders * 100 : null)] }))}/>}</div>;
}
