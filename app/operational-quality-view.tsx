"use client";
import { useCallback } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { buildForecastMatrix, emptyReportChoice, matchesOfficialSource } from "@/lib/forecast-reporting";
import { useForecastFilters, useOperationPin, useRead } from "./forecast-towell-app";

export function OperationalQualityView() {
  const { repository, filters } = useForecastFilters();
  const pin = useOperationPin();
  const read = useRead(useCallback(async () => {
    const source = await pin();
    if (!source) throw new Error("quality_source_missing");
    if (!repository.getReportSnapshot) throw new Error("quality_report_unavailable");
    const [snapshot, summary, evaluation] = await Promise.all([
      repository.getReportSnapshot(filters, source), repository.getHistoricalSummary(filters, source),
      filters.chainId && repository.previews?.qualityGates
        ? repository.previews.qualityGates(filters.chainId).then(value => ({ value, unavailable: false }))
          .catch(() => ({ value: null, unavailable: true }))
        : Promise.resolve({ value: null, unavailable: false }),
    ]);
    if (!snapshot.sourceContext || !matchesOfficialSource(snapshot.sourceContext, source)) throw new Error("quality_source_mismatch");
    if (repository.openOperationalRead && !matchesOfficialSource(await repository.openOperationalRead(), source)) throw new Error("quality_source_changed");
    const matrix = buildForecastMatrix(snapshot, emptyReportChoice, [null, null]);
    const coverage = (["STATISTICAL", "ML", "ENSEMBLE"] as const).map(family => ({ family,
      count: matrix.rows.filter(row => matrix.periods.length === 12 && matrix.periods.every(month => row.values[month]?.[family] !== null && row.values[month]?.[family] !== undefined)).length,
    }));
    return { source, summary, matrix, evaluation, coverage,
      pending: snapshot.products.filter(product => product.commercial?.status !== "APPROVED").length };
  }, [repository, filters, pin]));
  const result = read.data;
  return <section className="space-y-5"><div><h1 className="text-2xl font-semibold">Calidad de datos</h1>
    <p className="mt-1 text-sm text-slate-500">Lectura de la fuente oficial y resultados guardados. No ejecuta ni recalifica motores.</p></div>
    {read.loading && <p role="status">Consultando calidad del alcance seleccionado…</p>}
    {read.error && <p role="alert">No fue posible comprobar una fuente coherente. No se muestran datos anteriores como sustituto.</p>}
    {result && <>
      <p className="text-xs text-slate-500">Fuente RS vigente · {result.source?.source_hash ?? "Sin huella disponible"} · Generación {result.source?.generation ?? "Sin dato"} · Último real {result.summary.latestPeriod ?? "Sin dato"} · Corte de resultados {result.matrix.issue ?? "Sin cálculo compatible"}</p>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[["Productos del filtro", result.summary.productCount], ["Scopes con histórico", result.summary.scopeCount],
          ["Observaciones Venta / Pedido / Entrega", result.summary.observationCount], ["Clasificaciones pendientes", result.pending]].map(([label, value]) =>
          <Card key={label}><CardContent className="p-5"><p className="text-sm text-slate-500">{label}</p><p className="mt-2 text-2xl font-semibold">{Number(value).toLocaleString("es-MX")}</p></CardContent></Card>)}
      </div>
      <Card><CardContent className="p-5"><h2 className="font-semibold">Cobertura de resultados guardados H1–H12</h2>
        <p className="mt-1 text-xs text-slate-500">Mismas variantes y corte que Períodos. Una curva ausente permanece ausente; los filtros de meses históricos no reescriben sus horizontes.</p>
        <table className="mt-3 w-full text-left text-sm"><thead><tr><th className="p-2">Motor</th><th className="p-2">Variantes con doce horizontes</th><th className="p-2">Estado</th></tr></thead><tbody>
          {result.coverage.map(row => <tr key={row.family} className="border-t"><td className="p-2">{{ STATISTICAL: "Estadístico", ML: "Machine Learning", ENSEMBLE: "Ensemble" }[row.family]}</td>
            <td className="p-2">{row.count} / {result.matrix.rows.length}</td><td className="p-2">{row.count === result.matrix.rows.length && row.count > 0 ? "Completo" : row.count ? "Cobertura parcial" : "Sin cálculo compatible"}</td></tr>)}
        </tbody></table>
      </CardContent></Card>
      <Card><CardContent className="p-5"><h2 className="font-semibold">Evaluación de resultados guardados de la cadena</h2>
        <p className="mt-1 text-xs text-slate-500">Evaluación del resultado guardado del scope completo; no se presenta como WAPE del modelo filtrado ni se promedian métricas por producto.</p>
        {result.evaluation.value ? <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
          <div><dt>Calidad de datos del scope</dt><dd>{result.evaluation.value.data_quality.status}</dd></div>
          <div><dt>Calidad del forecast del scope</dt><dd>{result.evaluation.value.forecast_quality.status}</dd></div>
          <div><dt>Política</dt><dd>{result.evaluation.value.policy_version}</dd></div>
          <div><dt>Publicación</dt><dd>{result.evaluation.value.publication}</dd></div>
        </dl> : <p className="mt-3 text-sm">{filters.chainId ? result.evaluation.unavailable ? "Sin evaluación guardada disponible para esta cadena." : "Sin evaluación guardada." : "Selecciona una cadena para consultar su evaluación guardada."}</p>}
      </CardContent></Card>
      <p className="text-xs text-slate-500">Las clasificaciones pendientes no están aprobadas. Los vacíos no equivalen a cero. La visibilidad de un histórico no acredita emisión previa ni WAPE LIVE; no se publican Champions.</p>
    </>}
  </section>;
}
