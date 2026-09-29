import type { PreviewJob, PreviewScope } from "@/lib/forecast-preview";
import { percent, quantity } from "@/lib/forecast-chart-data";
import { currentRetrospectivePreview, previewMetrics } from "@/lib/preview-presentation";
import { horizonAccuracy } from "@/lib/statistical-preview-data";
import { ForecastGrid } from "./forecast-horizon-table";

export function PreviewPerformancePanel({ job, scope, productId, historical = false }: { job: PreviewJob | null; scope: PreviewScope | null; productId: string | null; historical?: boolean }) {
  if (!scope || !job) return <p className="rounded-xl border bg-white p-4 text-sm text-slate-500">Sin vista previa para este scope. Las métricas aparecen después de una corrida autorizada.</p>;
  if (!currentRetrospectivePreview(job, scope)) return <p role="status" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">Vista previa desactualizada. No se presentan métricas retrospectivas anteriores.</p>;
  const metrics = previewMetrics(scope, productId);
  const rows = [
    ["Estadístico", metrics.stat.model ?? "—", metrics.stat],
    ["Machine Learning", metrics.ml.model ?? "—", metrics.ml],
    ["Fcst Towell", metrics.towell.model ?? "—", metrics.towell],
  ] as const;
  const detail = [metrics.stat, metrics.ml].map((m, i) => {
    const candidate = m.candidate;
    return [i === 0 ? "Estadístico" : "ML", m.model ?? "—", percent(m.wape), percent(m.bias), quantity(candidate?.observations), candidate?.reason ?? candidate?.status ?? "Sin evidencia evaluada"];
  });
  const horizons = [1, 3, 6, 12].flatMap(h => rows.map(([name, , m]) => {
    const evidence = horizonAccuracy(m.candidate).find(row => row.horizon === h);
    const raw = evidence && m.candidate?.by_horizon?.find(row => row.horizon === h);
    return [`H${h}`, name, percent(evidence?.wape), percent(raw?.bias), quantity(raw?.observations)];
  }));
  return <section className="space-y-4" aria-label="Desempeño retrospectivo">
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{[
      ["WAPE Stat retrospectivo", metrics.stat.wape], ["WAPE ML retrospectivo", metrics.ml.wape],
      ["WAPE Fcst Towell retrospectivo", metrics.towell.wape], ["Bias Fcst Towell retrospectivo", metrics.towell.bias],
    ].map(([label, value]) => <div className="rounded-xl border border-slate-200 bg-white p-4" key={label}><p className="text-xs text-slate-500">{label}</p><p className="mt-2 text-2xl font-semibold">{percent(value as number | null)}</p></div>)}</div>
    <p className="text-xs text-slate-500">{productId ? "Métricas del producto seleccionado." : "Métricas del scope seleccionado; no se promedian métricas por producto."} Evaluación retrospectiva rolling-origin. No constituye certificación point-in-time.</p>
    {historical && <details className="rounded-xl border bg-white p-4"><summary className="cursor-pointer font-semibold">Ver desempeño de modelos</summary><div className="mt-4 space-y-4"><ForecastGrid headers={["Motor", "Modelo", "WAPE", "Bias", "Observaciones", "Estado / motivo"]} rows={detail}/><h3 className="text-sm font-semibold">Desempeño por horizonte disponible</h3><ForecastGrid headers={["Horizonte", "Motor", "WAPE", "Bias", "Observaciones"]} rows={horizons}/></div></details>}
  </section>;
}
