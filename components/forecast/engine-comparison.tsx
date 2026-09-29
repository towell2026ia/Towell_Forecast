import { retrospectiveMetric } from "@/lib/statistical-preview-data";
import { productCandidate } from "@/lib/ml-preview-data";
import { percent, quantity, type Horizon } from "@/lib/forecast-chart-data";
import type { PreviewCandidate, PreviewScope } from "@/lib/forecast-preview";
import { ForecastGrid } from "./forecast-horizon-table";
import { coverage, humanStatus } from "./forecast-summary";

const evidenceTip = "No existe evidencia retrospectiva suficiente para calcular esta métrica.";
function Candidates({ candidates, available, trained = [] }: { candidates: PreviewCandidate[]; available: string[]; trained?: PreviewCandidate[] }) {
  const names = [...new Set([...available, ...candidates.map(c => c.model).filter((m): m is string => Boolean(m)), ...trained.map(c => c.model).filter((m): m is string => Boolean(m))])];
  return <ForecastGrid headers={["Modelo", "Disponible", "WAPE retro", "Bias retro", "Estado / motivo"]} rows={names.map(name => { const c = candidates.find(r => r.model === name) ?? trained.find(r => r.model === name); return [name, c?.available === false ? "No" : available.includes(name) || c?.available ? "Sí" : "—", percent(retrospectiveMetric(c, "wape")), percent(retrospectiveMetric(c, "bias")), c?.reason ?? c?.status ?? "—"]; })}/>;
}
export function EngineComparison({ scope, horizons = [], productId }: { scope: PreviewScope; horizons?: Horizon[]; productId?: string | null }) {
  const total = scope.eligibility?.evaluated, stat = scope.statistical, ml = scope.ml, selection = scope.selection;
  const first = horizons.find(h => h.horizon === 1);
  const statModel = first && "statistical_model" in first ? first.statistical_model : null;
  const statMetric = stat?.candidates.find(c => c.product_id === productId && c.model === statModel);
  const mlMetric = productCandidate(ml?.candidates.find(c => c.model === ml.leader), productId);
  const previewMetric = productCandidate(selection?.preview_leader ?? undefined, productId);
  const distribution = Object.entries(stat?.models ?? {}).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  return <section className="space-y-4"><h2 className="font-semibold">Comparativa de motores</h2>
    <div title={evidenceTip}><ForecastGrid headers={["Motor", "Cobertura", "Modelo / estrategia", "WAPE retro", "Bias retro", "Pronóstico H1", "Estado"]} rows={[
      ["Estadístico", coverage(scope.eligibility?.stat_eligible, total), productId ? statModel ?? "—" : distribution[0] ? `${distribution[0][0]} · ${distribution[0][1]} productos` : "—", percent(productId ? retrospectiveMetric(statMetric, "wape") : stat?.retrospective_wape), percent(productId ? retrospectiveMetric(statMetric, "bias") : stat?.retrospective_bias), quantity(first && "statistical_value" in first ? first.statistical_value : null), humanStatus(stat?.status)],
      ["ML", coverage(scope.eligibility?.ml_eligible, total), ml?.leader ?? "—", percent(productId ? retrospectiveMetric(mlMetric, "wape") : ml?.retrospective_wape), percent(productId ? retrospectiveMetric(mlMetric, "bias") : ml?.retrospective_bias), quantity(first && "ml_value" in first ? first.ml_value : null), humanStatus(ml?.status)],
      ["Preview", "—", selection?.preview_leader?.model ?? selection?.preview_leader?.strategy ?? "—", percent(retrospectiveMetric(previewMetric, "wape")), percent(retrospectiveMetric(previewMetric, "bias")), quantity(first?.forecast_towell), "PREVIEW · No publicado"],
    ]}/></div>
    <div className="grid gap-3 rounded-xl border border-slate-200 bg-white p-4 text-sm sm:grid-cols-2 xl:grid-cols-5">{[["Champion publicado", selection?.published_champion?.version ?? "Ninguno"], ["Preview Leader", selection?.preview_leader?.model ?? selection?.preview_leader?.strategy ?? "—"], ["Challenger", selection?.preview_challenger?.model ?? selection?.preview_challenger?.strategy ?? "—"], ["No Degradation", selection?.no_degradation == null ? "N/A" : selection.no_degradation ? "PASS" : "FAIL"], ["Promoción automática", "OFF"]].map(([title, value]) => <div key={title}><p className="text-xs text-slate-500">{title}</p><p className="mt-1 font-semibold">{value}</p></div>)}</div>
    <details className="rounded-xl border bg-white p-4"><summary className="cursor-pointer text-sm font-semibold">Ver detalle estadístico</summary><p className="my-3 text-xs text-slate-500">Modelo más seleccionado: {distribution[0]?.[0] ?? "—"}. Es una distribución entre productos, no un Champion global.</p><ForecastGrid headers={["Modelo", "Productos"]} rows={distribution}/><div className="mt-3"><Candidates candidates={stat?.scope_candidates ?? stat?.candidates ?? []} available={stat?.available_candidates ?? []}/></div></details>
    <details className="rounded-xl border bg-white p-4"><summary className="cursor-pointer text-sm font-semibold">Ver detalle ML</summary><p className="my-3 text-sm">{humanStatus(ml?.status)} para el scope actual · {quantity(ml?.training_samples)} muestras disponibles · {quantity(scope.eligibility?.ml_eligible)} productos cumplen elegibilidad ML</p><Candidates candidates={ml?.candidates ?? []} available={ml?.available_candidates ?? []} trained={ml?.trained_candidates}/></details>
    <details className="rounded-xl border bg-white p-4"><summary className="cursor-pointer text-sm font-semibold">Detalles técnicos</summary><p className="mt-3 text-xs text-slate-500">{scope.engine_version ?? "—"} · {scope.mode ?? "—"} · {scope.status} · {scope.certification_status ?? "—"}</p><p className="mt-2 text-sm">{humanStatus(scope.certification_status)}</p><p className="mt-2 text-xs text-slate-500">Cold start: {quantity(scope.eligibility?.COLD_START)} · Inactive: {quantity(scope.eligibility?.INACTIVE)} · Pre-launch: {quantity(scope.eligibility?.["PRE-LAUNCH"])} · Insufficient: {quantity(scope.eligibility?.INSUFFICIENT)}</p></details>
  </section>;
}
